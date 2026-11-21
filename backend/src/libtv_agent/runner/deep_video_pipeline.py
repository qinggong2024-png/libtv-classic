"""Durable depth-first editing and explicitly supplied depth-video replacement."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.domain.enums import AttemptStatus, CommandStatus, GenerationStatus
from libtv_agent.services.deep_video_media import DEPTH_MODEL, inspect_reference_video

PIPELINE_VERSION = "depth-first-v1"


def pipeline_state(command) -> dict:
    return dict(command.command_manifest_json.get("depthPipeline") or {})


def save_pipeline(command, state: dict) -> None:
    command.command_manifest_json = {**command.command_manifest_json, "depthPipeline": state}


def depth_first(variant) -> bool:
    return variant.generation_config_json.get("deepVideoPipeline") == PIPELINE_VERSION


async def pipeline_request(session, command, attempt, task, variant, request,
                           adapter, object_store, settings) -> VideoGenerationRequest:
    if variant.generation_config_json.get("deepVideoPipeline") == "depth-input-v1":
        try:
            await validate_depth(request.reference_video_path, variant)
        except (ValueError, TimeoutError) as exc:
            raise KlingCliError(
                "WAVESPEED_DEPTH_INPUT_UNAVAILABLE", "已有深度视频校验失败，已停止替换",
            ) from exc
        if not pipeline_state(command):
            source = next(asset for asset in task.assets if asset.role == "REFERENCE_VIDEO")
            save_pipeline(command, {
                "version": "depth-input-v1", "stage": "replacement", "editModel": request.model,
                "depthObjectKey": source.object_key, "depthInput": "uploaded",
            })
        return request
    if not depth_first(variant):
        return request
    state = pipeline_state(command)
    if not state:
        state = {"version": PIPELINE_VERSION, "stage": "depth", "editModel": request.model}
        save_pipeline(command, state)
    if state.get("stage") == "depth":
        return replace(
            request, model=DEPTH_MODEL,
            prompt="Extract temporally consistent depth from the reference video.",
            image_paths=(), product_reference_paths=(), refer_image_paths=(),
            first_frame_path=None, last_frame_path=None,
        )
    if (state.get("stage") != "replacement" or not state.get("depthTaskId")
            or not state.get("depthOutputUrl")):
        raise KlingCliError("WAVESPEED_DEPTH_REQUIRED", "缺少已完成的深度视频，已阻止替换")
    destination = settings.runner_root / "depth-pipeline" / str(attempt.id) / "depth.mp4"
    try:
        if state.get("depthObjectKey"):
            depth_path = await object_store.localize(state["depthObjectKey"], destination)
        else:
            output = await adapter.download_media(
                url=state["depthOutputUrl"], destination=destination,
            )
            await validate_depth(output, variant)
            state["depthObjectKey"] = await object_store.put_file(
                output,
                f"users/{task.user_id}/intermediates/{task.id}/{attempt.id}/depth.mp4",
                "video/mp4",
            )
            save_pipeline(command, state)
            await session.commit()
            depth_path = output
        # Revalidate even a recovered object; never substitute the original video.
        await validate_depth(depth_path, variant)
    except Exception as exc:
        raise KlingCliError(
            "WAVESPEED_DEPTH_INPUT_UNAVAILABLE", "深度视频下载或校验失败，已停止替换",
            details={"errorType": type(exc).__name__}, submitted=True, uncertain=True,
        ) from exc
    return replace(request, reference_video_path=Path(depth_path), model=state["editModel"])


async def validate_depth(path, variant) -> None:
    metadata = await inspect_reference_video(path)
    source_duration = float(variant.generation_config_json["sourceDurationSeconds"])
    if (not 4 <= metadata.duration <= 15
            or abs(metadata.duration - source_duration) > 0.5):
        raise ValueError("深度视频时长与原片不匹配")


def record_pipeline_submission(command, task, submission) -> None:
    state = pipeline_state(command)
    if not state:
        return
    stage = state["stage"]
    state[f"{stage}TaskId"] = submission.provider_task_id
    state[f"{stage}SubmittedAt"] = datetime.now(UTC).isoformat()
    save_pipeline(command, state)
    task.settings_json = {**task.settings_json, "deepVideoStage": stage}


async def queue_replacement(session, command, attempt, variant, task, result) -> bool:
    state = pipeline_state(command)
    if not depth_first(variant) or state.get("stage") != "depth":
        return False
    if (attempt.provider_model != DEPTH_MODEL or state.get("depthTaskId") != attempt.task_id
            or result.status != "succeeded" or not result.outputs):
        raise KlingCliError("WAVESPEED_DEPTH_REQUIRED", "深度提取尚未成功，已阻止替换")
    state.update(
        stage="replacement", depthOutputUrl=result.outputs[0].url,
        depthCompletedAt=datetime.now(UTC).isoformat(),
    )
    save_pipeline(command, state)
    task.settings_json = {**task.settings_json, "deepVideoStage": "replacement"}
    # A new paid boundary gets its own durable launch marker. The depth handle is
    # retained above, and MUST NOT be mistaken for a replacement submission on recovery.
    attempt.task_id = None
    attempt.provider_model = state["editModel"]
    attempt.provider_status = "queued"
    attempt.provider_raw_status = None
    attempt.last_polled_at = None
    attempt.launch_started_at = None
    attempt.completed_at = None
    attempt.output_url = None
    attempt.raw_response_json = {}
    attempt.status = AttemptStatus.DISPATCH_PENDING
    attempt.state_version += 1
    command.status = CommandStatus.READY
    command.claim_owner = None
    command.lease_expires_at = None
    command.launch_started_at = None
    command.process_started_at = None
    variant.generation_status = GenerationStatus.RUNNING
    await session.commit()
    return True
