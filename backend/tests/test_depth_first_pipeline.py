"""Failure and recovery boundaries of the two paid video stages, using fake providers."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.video_provider import (
    ProviderOutput,
    ProviderTaskResult,
    VideoGenerationRequest,
)
from libtv_agent.domain.enums import AttemptStatus, CommandStatus, GenerationStatus
from libtv_agent.runner.deep_video_pipeline import (
    pipeline_request,
    pipeline_state,
    queue_replacement,
    save_pipeline,
)
from libtv_agent.runner.launch_recovery import recover_abandoned_claims
from libtv_agent.services.deep_video_media import DEPTH_MODEL, RECREATE_MODEL
from test_launch_started_recovery import (
    _settings,
    _stranded_command,
    _supervisor,
)
from test_launch_started_recovery import session as session  # noqa: F401


async def graph(session, tmp_path):
    settings = _settings(tmp_path)
    settings.wavespeed_enabled = True
    settings.wavespeed_api_key = "synthetic-test-only"
    task, variant, attempt, command, _ = await _stranded_command(
        session, provider=settings.wavespeed_video_provider_name,
        lease_expires_at=None,
    )
    task.workflow_kind = "DEEP_VIDEO"
    task.settings_json = {"deepVideoStage": "depth"}
    variant.generation_config_json = {
        "deepVideoPipeline": "depth-first-v1", "sourceDurationSeconds": 5.2,
    }
    attempt.status = AttemptStatus.RUNNING
    attempt.task_id = "depth-task"
    attempt.provider_model = DEPTH_MODEL
    attempt.submitted_at = datetime.now(UTC)
    command.status = CommandStatus.PROCESS_STARTED
    save_pipeline(command, {
        "stage": "depth", "editModel": RECREATE_MODEL, "depthTaskId": "depth-task",
    })
    await session.commit()
    return settings, task, variant, attempt, command


@pytest.mark.parametrize("status", ["running", "failed", "succeeded"])
async def test_no_edit_without_successful_depth_output(session, tmp_path, status):
    _, task, variant, attempt, command = await graph(session, tmp_path)
    result = ProviderTaskResult(status, status, (), {})
    with pytest.raises(KlingCliError, match="尚未成功"):
        await queue_replacement(session, command, attempt, variant, task, result)
    assert pipeline_state(command)["stage"] == "depth"
    assert attempt.task_id == "depth-task"
    assert command.status == CommandStatus.PROCESS_STARTED


async def test_failed_depth_never_becomes_a_deliverable(session, tmp_path):
    settings, _, variant, attempt, command = await graph(session, tmp_path)

    class Adapter:
        async def query(self, task_id):
            assert task_id == "depth-task"
            return ProviderTaskResult("failed", "failed", (), {}, message="depth failed")

    supervisor = _supervisor(tmp_path)
    supervisor.settings = settings
    supervisor._kling_adapters[settings.wavespeed_video_provider_name] = Adapter()
    assert await supervisor.poll_kling_tasks(session) == 1
    assert variant.generation_status == GenerationStatus.FAILED
    assert pipeline_state(command)["stage"] == "depth"
    assert attempt.output_url is None


@pytest.mark.parametrize("recover_unknown", [False, True])
async def test_depth_completion_and_claim_recovery_do_not_resubmit_depth(
    session, tmp_path, recover_unknown,
):
    settings, task, variant, attempt, command = await graph(session, tmp_path)
    result = ProviderTaskResult(
        "succeeded", "completed", (ProviderOutput("https://cdn.example/depth.mp4"),), {},
    )
    if recover_unknown:
        attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
        command.status = CommandStatus.UNCERTAIN
        await session.commit()

        class Adapter:
            async def query(self, task_id):
                assert task_id == "depth-task"
                return result

        supervisor = _supervisor(tmp_path)
        supervisor.settings = settings
        supervisor._kling_adapters[settings.wavespeed_video_provider_name] = Adapter()
        assert await supervisor.reconcile_attempt(session, attempt.id) == "RUNNING"
    else:
        assert await queue_replacement(session, command, attempt, variant, task, result)
    assert attempt.task_id is None
    assert attempt.provider_model == RECREATE_MODEL
    assert attempt.submitted_at is not None
    assert command.launch_started_at is None
    assert pipeline_state(command)["depthTaskId"] == "depth-task"
    command.status = CommandStatus.CLAIMED
    command.lease_expires_at = datetime.now(UTC) - timedelta(minutes=30)
    attempt.status = AttemptStatus.LAUNCHING
    await session.commit()
    assert await recover_abandoned_claims(session) == 1
    assert command.status == CommandStatus.READY
    assert attempt.status == AttemptStatus.DISPATCH_PENDING
    assert pipeline_state(command)["stage"] == "replacement"


async def test_lost_edit_submission_is_uncertain_not_queried_as_depth(session, tmp_path):
    settings, task, variant, attempt, command = await graph(session, tmp_path)
    await queue_replacement(session, command, attempt, variant, task, ProviderTaskResult(
        "succeeded", "completed", (ProviderOutput("https://cdn.example/depth.mp4"),), {},
    ))
    command.status = CommandStatus.LAUNCH_STARTED
    command.launch_started_at = datetime.now(UTC) - timedelta(minutes=30)
    command.lease_expires_at = command.launch_started_at
    attempt.status = AttemptStatus.LAUNCHING
    await session.commit()
    supervisor = _supervisor(tmp_path)
    supervisor.settings = settings
    assert await supervisor.recover_stale_launch_started_commands(session) == 1
    assert command.status == CommandStatus.UNCERTAIN
    assert attempt.status == AttemptStatus.RECONCILING
    assert attempt.task_id is None
    assert pipeline_state(command)["depthTaskId"] == "depth-task"


@pytest.mark.parametrize("broken", ["missing_provenance", "download", "object"])
async def test_depth_input_failure_blocks_edit_without_original_fallback(
    session, tmp_path, broken,
):
    settings, task, variant, attempt, command = await graph(session, tmp_path)
    save_pipeline(command, {
        "stage": "replacement", "editModel": RECREATE_MODEL,
        **({} if broken == "missing_provenance" else {
            "depthTaskId": "depth-task", "depthOutputUrl": "https://cdn.example/depth.mp4",
        }),
        **({"depthObjectKey": "missing-depth.mp4"} if broken == "object" else {}),
    })

    async def fail(**kwargs):
        raise OSError("synthetic unavailable")

    async def missing(key, destination):
        raise FileNotFoundError(key)

    adapter = SimpleNamespace(download_media=fail)
    store = SimpleNamespace(localize=missing)
    request = VideoGenerationRequest(
        "Use @Image 1", RECREATE_MODEL, reference_video_path=tmp_path / "original.mp4",
    )
    with pytest.raises(KlingCliError):
        await pipeline_request(
            session, command, attempt, task, variant, request, adapter, store, settings,
        )


async def test_combined_budget_rejects_when_only_edit_cost_fits(session):
    from fastapi import HTTPException
    from libtv_agent.domain.models import User
    from libtv_agent.services.account_access import reserve_usd

    user = User(
        username=f"budget-{uuid.uuid4()}", password_hash="disabled",
        daily_budget_microusd=1_200_000,
        depth_video_price_microusd=50_000, recreate_video_price_microusd=200_000,
    )
    session.add(user)
    await session.commit()
    with pytest.raises(HTTPException):
        await reserve_usd(
            session, user_id=user.id, kind="VIDEO", operation_id=uuid.uuid4(),
            units=6, pricing_category="RECREATE_DEPTH_VIDEO",
        )
