"""Fault injection at provider, subprocess, delivery and optional-subject boundaries."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.kling_official_cli import (
    KlingCliError,
    KlingOfficialCliAdapter,
    _confirmed_rate_limit_rejection,
    retry_kling_call,
)
from libtv_agent.adapters.libtv import GenerationResult
from libtv_agent.adapters.video_provider import ProviderOutput, ProviderTaskResult
from libtv_agent.config import Settings
from libtv_agent.domain.enums import (
    AttemptStatus,
    CommandStatus,
    DownloadStatus,
    LedgerEntryType,
    ParentTaskPhase,
)
from libtv_agent.domain.models import (
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    PointsLedger,
    SubjectElementJob,
)
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services import subject_elements
from libtv_agent.services.kling_elements import KlingElementError, _invoke
from libtv_agent.services.media import technical_validate
from sqlalchemy import select, update
from test_image_quota_concurrency import world  # noqa: F401
from test_launch_started_recovery import _stranded_command


@pytest.mark.parametrize("fault", ["exception", "empty_output"])
async def test_bad_poll_does_not_starve_other_jobs_or_lose_paid_handle(world, fault):  # noqa: F811
    app, factory, _, _ = world
    settings = app.state.settings.model_copy(update={"kling_cli_enabled": True})
    identifiers = []
    async with factory() as session:
        for index in range(2):
            _, _, attempt, command, _ = await _stranded_command(
                session, provider=settings.kling_cli_provider_name, lease_expires_at=None
            )
            attempt.status = AttemptStatus.RUNNING
            attempt.task_id = f"original-{index}"
            command.status = CommandStatus.PROCESS_STARTED
            identifiers.append(attempt.id)
        await session.commit()

    queries = []

    class Adapter:
        async def query(self, task_id):
            queries.append(task_id)
            if task_id == "original-0":
                if fault == "exception":
                    raise RuntimeError("private-provider-body-must-not-be-persisted")
                return ProviderTaskResult("succeeded", "COMPLETED", (), {})
            return ProviderTaskResult("running", "RUNNING", (), {})

    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[settings.kling_cli_provider_name] = Adapter()
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 2
    assert set(queries) == {"original-0", "original-1"}
    async with factory() as session:
        first = await session.get(GenerationAttempt, identifiers[0])
        second = await session.get(GenerationAttempt, identifiers[1])
        assert first.status.value in {"RUNNING", "RECONCILING"}
        assert second.status == AttemptStatus.RUNNING
        assert first.task_id == "original-0"
        assert first.last_polled_at is not None
        assert "private-provider" not in str(first.error_message)
        assert first.last_error_code == (
            "VIDEO_PROVIDER_POLL_FAILED" if fault == "exception" else "VIDEO_OUTPUT_NOT_READY"
        )


async def test_background_download_recovery_observes_cooldown(world, monkeypatch):  # noqa: F811
    app, factory, _, _ = world
    async with factory() as session:
        _, variant, attempt, _, _ = await _stranded_command(
            session, provider=app.state.settings.kling_cli_provider_name, lease_expires_at=None
        )
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.task_id = "paid-handle"
        attempt.output_url = "https://synthetic.invalid/video.mp4"
        attempt.last_error_code = "DOWNLOAD_RETRYABLE"
        variant.download_status = DownloadStatus.FAILED
        await session.commit()
        identifier = attempt.id
        await session.execute(
            update(GenerationAttempt)
            .where(GenerationAttempt.id == identifier)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()

    recovered = []

    async def fail_delivery(session, command, attempt, result, **kwargs):
        recovered.append(attempt.task_id)
        attempt.updated_at = datetime.now(UTC)
        await session.commit()

    supervisor = RunnerSupervisor(object(), object(), app.state.settings)
    supervisor._kling_adapters[app.state.settings.kling_cli_provider_name] = SimpleNamespace(
        query=AsyncMock(
            return_value=ProviderTaskResult(
                "succeeded",
                "COMPLETED",
                (ProviderOutput("https://synthetic.invalid/fresh.mp4"),),
                {},
            )
        )
    )
    monkeypatch.setattr(supervisor, "_handle_success", fail_delivery)
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 1
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 0
        variant = await session.get(GenerationVariant, variant.id)
        assert variant.download_status == DownloadStatus.FAILED
    assert recovered == ["paid-handle"]


@pytest.mark.parametrize("fault", ["exception", "empty_output"])
async def test_delivery_refresh_failure_keeps_original_handle_and_cooldown(
    world,  # noqa: F811
    monkeypatch,
    fault,  # noqa: F811
):
    app, factory, _, _ = world
    async with factory() as session:
        _, variant, attempt, _, _ = await _stranded_command(
            session, provider=app.state.settings.kling_cli_provider_name, lease_expires_at=None
        )
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.task_id = "original-paid-task"
        attempt.output_url = "https://synthetic.invalid/expired.mp4"
        attempt.last_error_code = "DOWNLOAD_RETRYABLE"
        variant.download_status = DownloadStatus.FAILED
        await session.commit()
        await session.execute(
            update(GenerationAttempt)
            .where(GenerationAttempt.id == attempt.id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()
        attempt_id = attempt.id
    query = AsyncMock(return_value=ProviderTaskResult("succeeded", "COMPLETED", (), {}))
    if fault == "exception":
        query.side_effect = RuntimeError("private-provider-body")
    supervisor = RunnerSupervisor(object(), object(), app.state.settings)
    supervisor._kling_adapters[app.state.settings.kling_cli_provider_name] = SimpleNamespace(
        query=query
    )
    delivery = AsyncMock()
    monkeypatch.setattr(supervisor, "_handle_success", delivery)
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 0
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 0
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.SUCCEEDED
        assert attempt.task_id == "original-paid-task"
        assert attempt.last_error_code == "DOWNLOAD_RETRYABLE"
        assert "private-provider" not in str(attempt.error_message)
    delivery.assert_not_awaited()
    query.assert_awaited_once_with("original-paid-task")


@pytest.mark.parametrize("account_state", ["mismatch", "matched"])
async def test_official_api_download_refresh_requires_original_paid_account(
    world,  # noqa: F811
    monkeypatch, account_state,
):
    app, factory, _, _ = world
    provider = app.state.settings.kling_api_provider_name
    async with factory() as session:
        _, variant, attempt, _, _ = await _stranded_command(
            session, provider=provider, lease_expires_at=None,
        )
        attempt.provider_account_id = "original-account"
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.task_id = "original-cloud-handle"
        attempt.output_url = "https://synthetic.invalid/expired.mp4"
        attempt.last_error_code = "DOWNLOAD_RETRYABLE"
        variant.download_status = DownloadStatus.FAILED
        await session.commit()
        await session.execute(
            update(GenerationAttempt).where(
                GenerationAttempt.id == attempt.id,
            ).values(updated_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()
        attempt_id = attempt.id
    adapter = SimpleNamespace(
        submission_account_id=AsyncMock(return_value=(
            "wrong-account" if account_state == "mismatch" else "original-account"
        )),
        query=AsyncMock(return_value=ProviderTaskResult(
            "succeeded", "COMPLETED",
            (ProviderOutput("https://synthetic.invalid/refreshed.mp4"),), {},
        )),
        submit=AsyncMock(),
    )
    supervisor = RunnerSupervisor(object(), object(), app.state.settings)
    supervisor._kling_adapters[provider] = adapter
    delivery = AsyncMock()
    monkeypatch.setattr(supervisor, "_handle_success", delivery)
    async with factory() as session:
        processed = await supervisor.resume_incomplete_downloads(session)
    assert processed == (0 if account_state == "mismatch" else 1)
    async with factory() as session:
        original = await session.get(GenerationAttempt, attempt_id)
        assert original.task_id == "original-cloud-handle"
        assert original.status == AttemptStatus.SUCCEEDED
        assert original.charge_status is not None
    if account_state == "mismatch":
        adapter.query.assert_not_awaited()
        delivery.assert_not_awaited()
    else:
        adapter.query.assert_awaited_once_with("original-cloud-handle")
        delivery.assert_awaited_once()
    adapter.submit.assert_not_awaited()


async def test_cancelled_task_is_excluded_from_delivery_recovery(world, monkeypatch):  # noqa: F811
    app, factory, _, _ = world
    async with factory() as session:
        task, variant, attempt, _, _ = await _stranded_command(
            session, provider=app.state.settings.kling_cli_provider_name, lease_expires_at=None
        )
        task.phase = ParentTaskPhase.CANCELLED
        attempt.status = AttemptStatus.SUCCEEDED
        variant.download_status = DownloadStatus.DOWNLOADING
        await session.commit()
    supervisor = RunnerSupervisor(object(), object(), app.state.settings)
    delivery = AsyncMock()
    monkeypatch.setattr(supervisor, "_handle_success", delivery)
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 0
    delivery.assert_not_awaited()


@pytest.mark.parametrize("fault", ["download", "probe", "storage"])
async def test_delivery_fault_recovers_without_resubmit_or_double_charge(
    world,  # noqa: F811
    monkeypatch,
    fault,  # noqa: F811
):
    app, factory, _, _ = world
    async with factory() as session:
        _, variant, attempt, command, _ = await _stranded_command(
            session, provider=app.state.settings.kling_cli_provider_name, lease_expires_at=None
        )
        attempt.credits_consumed = 120
        attempt.task_id = "original-paid-task"
        await session.commit()
        attempt_id, variant_id, command_id = attempt.id, variant.id, command.id

    failing = True

    async def download(*args, **kwargs):
        if failing and fault == "download":
            raise OSError("synthetic interrupted download")

    async def validate(*args, **kwargs):
        if failing and fault == "probe":
            raise TimeoutError("synthetic stuck probe")
        return SimpleNamespace(
            sha256="a" * 64,
            size_bytes=1024,
            duration_seconds=5,
            width=1080,
            height=1920,
            has_audio=False,
            passed=True,
            issue_codes=(),
        )

    async def put_file(*args):
        if failing and fault == "storage":
            raise OSError("synthetic object store unavailable")
        return "synthetic/output.mp4"

    monkeypatch.setattr("libtv_agent.runner.supervisor.download_with_retries", download)
    monkeypatch.setattr("libtv_agent.runner.supervisor.technical_validate", validate)
    supervisor = RunnerSupervisor(object(), SimpleNamespace(put_file=put_file), app.state.settings)
    query = AsyncMock(
        return_value=ProviderTaskResult(
            "succeeded", "COMPLETED", (ProviderOutput("https://synthetic.invalid/fresh.mp4"),), {}
        )
    )
    supervisor._kling_adapters[app.state.settings.kling_cli_provider_name] = SimpleNamespace(
        query=query
    )
    async with factory() as session:
        await supervisor._handle_success(
            session,
            await session.get(type(command), command_id),
            await session.get(GenerationAttempt, attempt_id),
            GenerationResult(
                status="SUCCEEDED",
                payload={
                    "taskId": "original-paid-task",
                    "videoUrl": "https://synthetic.invalid/video.mp4",
                },
            ),
        )
    async with factory() as session:
        variant = await session.get(GenerationVariant, variant_id)
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert variant.download_status == DownloadStatus.FAILED
        assert attempt.status == AttemptStatus.SUCCEEDED
        assert attempt.last_error_code == "DOWNLOAD_RETRYABLE"
        assert await supervisor.resume_incomplete_downloads(session) == 0
        await session.execute(
            update(GenerationAttempt)
            .where(GenerationAttempt.id == attempt_id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=2))
        )
        await session.commit()
    failing = False
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 1
    async with factory() as session:
        assert await supervisor.resume_incomplete_downloads(session) == 0
        variant = await session.get(GenerationVariant, variant_id)
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert variant.download_status == DownloadStatus.READY
        assert attempt.task_id == "original-paid-task"
        assert attempt.output_url == "https://synthetic.invalid/fresh.mp4"
        assert attempt.last_error_code is None
        assert len(list(await session.scalars(select(DownloadArtifact)))) == 1
        entries = list(await session.scalars(select(PointsLedger)))
        assert [e.entry_type for e in entries].count(LedgerEntryType.CONSUME) == 1
    query.assert_awaited_once_with("original-paid-task")


@pytest.mark.parametrize("status_code,retry", [(501, False), (409, False), (503, True)])
async def test_subject_failure_recovery_respects_permanent_errors(
    world,  # noqa: F811
    monkeypatch,
    status_code,
    retry,  # noqa: F811
):
    app, factory, _, _ = world
    async with factory() as session:
        task, _, _, _, _ = await _stranded_command(
            session, provider=app.state.settings.kling_cli_provider_name, lease_expires_at=None
        )
        job = SubjectElementJob(
            parent_task_id=task.id,
            user_id=task.user_id,
            external_task_id=str(task.id),
            name="synthetic",
            description="synthetic",
            status="PROCESSING",
            attempt_count=1,
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    app.state.database.session_factory = factory
    scheduled = []
    monkeypatch.setattr(
        subject_elements,
        "schedule_subject_element_job",
        lambda *args, **kwargs: scheduled.append(kwargs) or True,
    )
    assert (
        await subject_elements._record_subject_element_failure(
            app,
            job_id=job_id,
            cause=KlingElementError(
                "SYNTHETIC",
                "synthetic",
                status_code=status_code,
            ),
        )
        is retry
    )
    assert await subject_elements.recover_subject_element_jobs(app) == int(retry)
    assert len(scheduled) == int(retry)


@pytest.mark.parametrize("submitted,uncertain", [(True, False), (False, True)])
async def test_generic_retry_never_replays_possibly_paid_operation(submitted, uncertain):
    calls = []

    async def operation():
        calls.append(1)
        raise KlingCliError(
            "KLING_RATE_LIMITED",
            "ambiguous",
            retryable=True,
            submitted=submitted,
            uncertain=uncertain,
        )

    with pytest.raises(KlingCliError):
        await retry_kling_call(operation, base_delay=0)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"generationId": "accepted"}},
        {"results": [{"task_id": "accepted"}]},
        {"data": {"requestAccepted": True}},
    ],
)
def test_nested_acceptance_evidence_disables_http_429_replay(body):
    assert not _confirmed_rate_limit_rejection({"ok": False, "status": 429, "body": body})


@pytest.mark.parametrize(
    "cause,status",
    [
        (RuntimeError("temporary transport failure"), 503),
        (ValueError("invalid local input"), 400),
        (KlingCliError("REJECTED", "permanent", retryable=False), 409),
        (KlingCliError("UNAVAILABLE", "temporary", retryable=True), 503),
    ],
)
async def test_subject_wrapper_preserves_retry_classification(cause, status):
    adapter = SimpleNamespace(create_element=AsyncMock(side_effect=cause))
    with pytest.raises(KlingElementError) as captured:
        await _invoke(adapter, "create_element", object())
    assert captured.value.status_code == status


class HangingProcess:
    def __init__(self):
        self.returncode = None
        self.started = asyncio.Event()
        self.exited = asyncio.Event()
        self.reaped = False
        self.kills = 0

    async def communicate(self):
        self.started.set()
        if self.returncode is None:
            await self.exited.wait()
        self.reaped = True
        return b"", b""

    def kill(self):
        self.kills += 1
        self.returncode = -9
        self.exited.set()

    async def wait(self):
        self.reaped = True
        return self.returncode


async def test_cancelled_cli_process_is_killed_and_reaped(tmp_path, monkeypatch):
    process = HangingProcess()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    adapter = KlingOfficialCliAdapter(Settings(_env_file=None, runner_root=tmp_path))
    worker = asyncio.create_task(adapter._run_process(["query_tasks", "original"], 60))
    await asyncio.wait_for(process.started.wait(), 2)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert process.kills == 1 and process.reaped


@pytest.mark.parametrize("cancel", [False, True])
async def test_media_probe_is_bounded_and_reaped(tmp_path, monkeypatch, cancel):
    process = HangingProcess()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("libtv_agent.services.media.shutil.which", lambda _: "ffprobe")
    worker = asyncio.create_task(
        technical_validate(tmp_path / "file.mp4", probe_timeout_seconds=0.01 if not cancel else 60)
    )
    await asyncio.wait_for(process.started.wait(), 2)
    if cancel:
        worker.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await worker
    assert process.kills == 1 and process.reaped
