from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime

from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.video_provider import VideoProviderAdapter
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import sha256_json
from libtv_agent.domain.enums import ParentTaskPhase, VariantKind
from libtv_agent.domain.models import Asset, AuditEvent, GenerationVariant, ParentTask, StagedUpload
from libtv_agent.domain.schemas import DeepVideoTaskCreate
from libtv_agent.services.brand_identity import append_brand_reference
from libtv_agent.services.cutflow_site import CutflowSiteError, creation_snapshot
from libtv_agent.services.deep_video_media import (
    DEPTH_MODEL,
    FLASH_RECREATE_MODEL,
    inspect_reference_video,
)
from libtv_agent.services.deep_video_prompts import PROMPT_VERSION, compile_recreation_prompt
from libtv_agent.services.fixed_video_tasks import FixedVideoTaskValidationError, queue_video_task
from libtv_agent.services.tasks import get_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def create_deep_video_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: DeepVideoTaskCreate,
    settings: Settings,
    kling_adapter: VideoProviderAdapter,
    object_store: ObjectStore,
) -> ParentTask:
    if not payload.paid_submission_consent:
        raise FixedVideoTaskValidationError(
            "PAID_SUBMISSION_CONSENT_REQUIRED",
            "请明确同意提交 WaveSpeed 付费处理任务",
        )
    try:
        site_snapshot = await creation_snapshot(session, user_id, payload.site_profile_id)
    except CutflowSiteError as exc:
        raise FixedVideoTaskValidationError("CUTFLOW_SITE_REQUIRED", str(exc)) from exc
    if payload.mode == "recreate":
        if "[参考图已移除，请重新选择]" in payload.prompt or any(
            not 1 <= int(number) <= len(payload.subject_images)
            for number in re.findall(r"@Image\s*(\d+)\b", payload.prompt, flags=re.IGNORECASE)
        ):
            raise FixedVideoTaskValidationError(
                "IMAGE_REFERENCE_INVALID", "提示词引用的参考图不存在，请重新选择目标参考图",
            )
    references = sorted(payload.subject_images, key=lambda i: i.order)
    background_id = payload.background_image_upload_id
    ids = [
        payload.reference_video_upload_id,
        *(i.upload_id for i in references),
        *([background_id] if background_id else []),
    ]
    # Claim uploads under a write lock as SQLite ignores SELECT FOR UPDATE.
    await session.execute(
        update(StagedUpload)
        .where(
            StagedUpload.id.in_(ids),
            StagedUpload.user_id == user_id,
        )
        .values(
            claimed_task_id=StagedUpload.claimed_task_id,
        )
        .execution_options(synchronize_session=False)
    )
    uploads = list(
        await session.scalars(
            select(StagedUpload)
            .where(
                StagedUpload.id.in_(ids),
                StagedUpload.user_id == user_id,
                StagedUpload.claimed_task_id.is_(None),
            )
            .with_for_update()
        )
    )
    by_id = {upload.id: upload for upload in uploads}
    if set(by_id) != set(ids):
        raise FixedVideoTaskValidationError(
            "UPLOAD_UNAVAILABLE", "参考素材不属于当前账号或已被使用"
        )
    video = by_id[payload.reference_video_upload_id]
    if video.mime_type not in {"video/mp4", "video/quicktime"}:
        raise FixedVideoTaskValidationError("INPUT_INVALID", "请上传有效的参考视频")
    if any(not by_id[i.upload_id].mime_type.startswith("image/") for i in references):
        raise FixedVideoTaskValidationError("INPUT_INVALID", "目标素材必须是图片")
    local = await object_store.localize(
        video.object_key,
        settings.runner_root
        / "deep-video"
        / str(video.id)
        / ("reference.mov" if video.mime_type == "video/quicktime" else "reference.mp4"),
    )
    try:
        metadata = await inspect_reference_video(local)
    except (ValueError, TimeoutError) as exc:
        raise FixedVideoTaskValidationError("INPUT_INVALID", str(exc)) from exc
    if payload.mode == "recreate" and not 4 <= metadata.duration <= 15:
        raise FixedVideoTaskValidationError(
            "INPUT_INVALID", "Video Edit 人物与商品替换需要 4–15 秒视频，请先裁剪后上传"
        )
    task_id = uuid.uuid4()
    now = datetime.now(UTC)
    model = DEPTH_MODEL if payload.mode == "depth" else settings.wavespeed_recreate_video_model
    duration_seconds = math.ceil(metadata.duration)
    if payload.mode == "depth":
        estimated_billable_seconds = max(3, duration_seconds)
    else:
        estimated_billable_seconds = (
            duration_seconds if model == FLASH_RECREATE_MODEL else duration_seconds * 2
        )
        if payload.reference_video_type == "original":
            estimated_billable_seconds += duration_seconds
    prompt = (
        "Extract temporally consistent depth from the reference video."
        if payload.mode == "depth"
        else compile_recreation_prompt(
            len(references), has_background=background_id is not None, requirement=payload.prompt,
        )
    )
    prompt_version = PROMPT_VERSION if payload.mode == "recreate" else "deep-video-v1"
    if payload.mode == "recreate" and site_snapshot:
        prompt = append_brand_reference(prompt, site_snapshot["brandReference"])
    config = {
        "generationProvider": settings.wavespeed_video_provider_name,
        "generationMode": "deep_video",
        "deepVideoMode": payload.mode,
        "referenceVideoType": payload.reference_video_type,
        "model": model,
        "durationSeconds": duration_seconds,
        "estimatedBillableSeconds": estimated_billable_seconds,
        "aspectRatio": (
            str(metadata.width / metadata.height)
            if payload.mode == "depth"
            else payload.aspect_ratio
        ),
        "sourceWidth": metadata.width,
        "sourceHeight": metadata.height,
        "sourceDurationSeconds": metadata.duration,
        "audio": "original",
        **({"resolution": "720p", "generateAudio": False} if payload.mode == "recreate" else {}),
        "multiShot": False,
        "resultCount": 1,
    }
    if payload.mode == "recreate" and payload.reference_video_type == "original":
        config.update(
            deepVideoPipeline="depth-first-v1", depthModel=DEPTH_MODEL,
            deepVideoStage="depth",
        )
    if payload.mode == "recreate":
        if payload.reference_video_type == "depth":
            config.update(deepVideoPipeline="depth-input-v1", deepVideoStage="replacement")
    task = ParentTask(
        id=task_id,
        user_id=user_id,
        client_task_id=payload.client_task_id,
        title=payload.title,
        asset_count=len(ids),
        workflow_kind="DEEP_VIDEO",
        site_profile_id=site_snapshot["profileId"] if site_snapshot else None,
        site_profile_version=site_snapshot["profileVersion"] if site_snapshot else None,
        phase=ParentTaskPhase.PREPARING,
        settings_json={**config, **({"cutflowSite": site_snapshot} if site_snapshot else {})},
        material_validation_json={"valid": True, "errors": [], "warnings": []},
        compiled_plan_json={
            "compiler": prompt_version, "model": model, "prompt": prompt,
            **({"stages": ([DEPTH_MODEL, model] if payload.reference_video_type == "original"
                           else [model])} if payload.mode == "recreate" else {}),
        },
        paid_submission_consent_at=now,
        user_requirement_raw=payload.prompt,
        user_requirement_normalized=payload.prompt,
    )
    task.assets = []
    for index, upload_id in enumerate(ids):
        upload = by_id[upload_id]
        is_video = index == 0
        is_background = background_id is not None and upload_id == background_id
        task.assets.append(Asset(
            parent_task_id=task_id,
            order_no=index,
            source_type="REFERENCE_VIDEO" if is_video else "SUBJECT_REFERENCE",
            role=(
                "REFERENCE_VIDEO" if is_video
                else "BACKGROUND_REFERENCE" if is_background else "PRODUCT"
            ),
            required=True,
            original_filename=upload.original_filename,
            sha256=upload.sha256,
            mime_type=upload.mime_type,
            size_bytes=upload.size_bytes,
            width=upload.width,
            height=upload.height,
            object_key=upload.object_key,
            thumbnail_key=upload.thumbnail_key,
            used_for_analysis=False,
            used_for_generation=True,
        ))
    task.variants = [
        GenerationVariant(
            variant_type=VariantKind.STABLE,
            revision=1,
            variant_index=1,
            variant_key="result-1",
            prompt_text=prompt,
            prompt_hash=sha256_json(prompt),
            prompt_template_version=prompt_version,
            generation_config_json=config,
            generation_config_hash=sha256_json(config),
            ordered_storyboard_assets_hash=sha256_json([by_id[i].sha256 for i in ids]),
            confirmed_at=now,
        )
    ]
    session.add(task)
    await session.flush()
    for upload in uploads:
        upload.claimed_task_id = task_id
    session.add(
        AuditEvent(
            actor_id=user_id,
            action="CREATE_DEEP_VIDEO_TASK",
            entity_type="parent_task",
            entity_id=str(task_id),
            to_state="PREPARING",
            reason="explicit WaveSpeed paid consent",
            correlation_id=str(uuid.uuid4()),
            evidence_refs_json=[],
        )
    )
    await session.commit()
    task = await get_task(session, user_id, task_id)
    return await queue_video_task(
        session,
        task=task,
        user_id=user_id,
        settings=settings,
        kling_adapter=kling_adapter,
        object_store=object_store,
    )
