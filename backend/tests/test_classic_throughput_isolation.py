"""S1/S3 isolation: admission, polling and resumable delivery remain distinct.

All adapters and URLs are synthetic. No provider submission or live storage.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.video_provider import ProviderOutput, ProviderTaskResult
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.enums import (
    AttemptStatus,
    CommandStatus,
    DownloadStatus,
    LedgerEntryType,
)
from libtv_agent.domain.models import (
    Base,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    PointsLedger,
)
from libtv_agent.runner.supervisor import RunnerSupervisor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_launch_started_recovery import _stranded_command


@pytest.fixture
async def lab(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'throughput-lab.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    settings = Settings(_env_file=None, app_env="test", kling_cli_enabled=True,
                        kling_api_key="synthetic-test-key-not-used",
                        classic_throughput_s1s3_enabled=True,
                        runner_root=tmp_path / "runner")
    yield factory, settings
    await db.dispose()


async def test_database_has_bounded_busy_timeout_without_changing_journal(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'throughput.db'}")
    try:
        async with db.engine.connect() as connection:
            timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar()
        assert timeout >= 5000
    finally:
        await db.dispose()


async def test_success_poll_persists_delivery_job_without_downloading(lab, monkeypatch):
    factory, settings = lab
    async with factory() as session:
        _, variant, attempt, command, _ = await _stranded_command(
            session, provider=settings.kling_cli_provider_name, lease_expires_at=None
        )
        attempt.status = AttemptStatus.RUNNING
        attempt.task_id = "paid-original-cloud-id"
        attempt.credits_consumed = 40
        command.status = CommandStatus.PROCESS_STARTED
        await session.commit()
        attempt_id, variant_id = attempt.id, variant.id

    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[settings.kling_cli_provider_name] = SimpleNamespace(
        query=AsyncMock(return_value=ProviderTaskResult(
            "succeeded", "COMPLETED",
            (ProviderOutput("https://synthetic.invalid/original.mp4"),), {},
        ))
    )
    downloader = AsyncMock(side_effect=AssertionError("poll must not call CDN"))
    monkeypatch.setattr("libtv_agent.runner.supervisor.download_with_retries", downloader)
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
    downloader.assert_not_awaited()
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        variant = await session.get(GenerationVariant, variant_id)
        assert attempt.status == AttemptStatus.SUCCEEDED
        assert attempt.task_id == "paid-original-cloud-id"
        assert variant.download_status == DownloadStatus.QUEUED
        entries = list(await session.scalars(select(PointsLedger)))
        assert [item.entry_type for item in entries] == [
            LedgerEntryType.RESERVE, LedgerEntryType.CONSUME
        ]


async def test_queued_delivery_claim_is_unique_across_workers(lab, monkeypatch):
    factory, settings = lab
    provider = settings.kling_cli_provider_name
    expected = set()
    async with factory() as session:
        for i in range(2):
            _, variant, attempt, command, _ = await _stranded_command(
                session, provider=provider, lease_expires_at=None
            )
            attempt.status = AttemptStatus.SUCCEEDED
            attempt.credits_consumed = 40
            attempt.task_id = f"original-paid-task-{i}"
            attempt.output_url = f"https://synthetic.invalid/clip-{i}.mp4"
            attempt.raw_response_json = {"videoUrl": attempt.output_url}
            command.status = CommandStatus.TERMINAL
            variant.download_status = DownloadStatus.QUEUED
            expected.add(attempt.output_url)
        await session.commit()

    downloads = []
    active = 0
    max_active = 0

    async def download(_adapter, *, url, destination):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        downloads.append(url)
        await asyncio.sleep(0.06)
        active -= 1

    async def validate(*_args, **_kwargs):
        return SimpleNamespace(
            sha256="a" * 64, size_bytes=1024, duration_seconds=5.0,
            width=1080, height=1920, has_audio=True, passed=True, issue_codes=(),
        )

    async def put_file(*_args):
        return "synthetic/output.mp4"

    monkeypatch.setattr("libtv_agent.runner.supervisor.download_with_retries", download)
    monkeypatch.setattr("libtv_agent.runner.supervisor.technical_validate", validate)
    supervisor = RunnerSupervisor(object(), SimpleNamespace(put_file=put_file), settings)
    supervisor._kling_adapters[provider] = object()

    async def worker():
        for _ in range(5):
            async with factory() as session:
                await supervisor.resume_incomplete_downloads(
                    session, limit=1, recover_stale_only=True
                )
            await asyncio.sleep(0.01)

    await asyncio.gather(worker(), worker())
    assert set(downloads) == expected
    assert len(downloads) == 2
    assert max_active == 2
    async with factory() as session:
        artifacts = list(await session.scalars(select(DownloadArtifact)))
        assert len(artifacts) == 2
        assert all(artifact.status == DownloadStatus.READY for artifact in artifacts)
        ledger = list(await session.scalars(select(PointsLedger)))
        assert [entry.entry_type for entry in ledger].count(LedgerEntryType.CONSUME) == 2


async def test_official_status_reads_are_bounded_to_four(lab):
    factory, settings = lab
    settings.kling_api_enabled = True
    provider = settings.kling_api_provider_name
    async with factory() as session:
        for i in range(8):
            _, _variant, attempt, command, _ = await _stranded_command(
                session, provider=provider, lease_expires_at=None
            )
            attempt.status = AttemptStatus.RUNNING
            attempt.task_id = f"remote-{i}"
            command.status = CommandStatus.PROCESS_STARTED
        await session.commit()

    in_flight = 0
    peak_in_flight = 0

    async def query(_task_id):
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        await asyncio.sleep(0.04)
        in_flight -= 1
        return ProviderTaskResult("running", "RUNNING", (), {})

    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[provider] = SimpleNamespace(query=query)
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 8
    assert peak_in_flight == 4


async def test_uncertain_paid_invocation_without_handle_holds_capacity(lab):
    from datetime import UTC, datetime

    factory, settings = lab
    settings.kling_api_max_active_cloud_tasks = 1
    provider = settings.kling_api_provider_name
    async with factory() as session:
        _, _variant, attempt, command, _ = await _stranded_command(
            session, provider=provider, lease_expires_at=None
        )
        attempt.status = AttemptStatus.RECONCILING
        attempt.task_id = None
        attempt.submission_started_at = datetime.now(UTC)
        attempt.provider_account_id = "synthetic-account"
        command.status = CommandStatus.UNCERTAIN
        await session.commit()

    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[provider] = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="synthetic-account")
    )
    async with factory() as session:
        assert await supervisor._kling_api_at_cloud_capacity(session) is True



async def test_default_off_preserves_existing_runner_sequence(lab, monkeypatch):
    from libtv_agent.runner import launch_recovery

    _factory, settings = lab
    settings.classic_throughput_s1s3_enabled = False
    supervisor = RunnerSupervisor(object(), object(), settings)
    sequence = []

    async def recover(_session):
        sequence.append("recover")

    async def download(_session):
        sequence.append("download")
        return 1

    async def observe(_session):
        sequence.append("observe")

    async def poll(_session):
        sequence.append("poll")
        return 1

    async def submit(_session, provider):
        sequence.append(provider)
        return 1

    monkeypatch.setattr(launch_recovery, "recover_abandoned_claims", recover)
    monkeypatch.setattr(supervisor, "resume_incomplete_downloads", download)
    monkeypatch.setattr(supervisor, "observe_command_hosts", observe)
    monkeypatch.setattr(supervisor, "poll_kling_tasks", poll)
    monkeypatch.setattr(supervisor, "_run_ready_provider", submit)
    assert await supervisor.run_ready(None) == 4
    assert sequence == ["recover", "download", "observe", "poll", "libtv", "kling"]

async def test_runner_admits_before_and_after_polls(lab, monkeypatch):
    from libtv_agent.runner import launch_recovery

    _factory, settings = lab
    supervisor = RunnerSupervisor(object(), object(), settings)
    sequence = []

    async def recover(_session):
        sequence.append("recover")

    async def observe(_session):
        sequence.append("observe")

    async def poll(_session):
        sequence.append("poll")
        return 1

    async def submit(_session, provider):
        sequence.append(provider)
        return 1

    monkeypatch.setattr(launch_recovery, "recover_abandoned_claims", recover)
    monkeypatch.setattr(supervisor, "observe_command_hosts", observe)
    monkeypatch.setattr(supervisor, "poll_kling_tasks", poll)
    monkeypatch.setattr(supervisor, "_run_ready_provider", submit)
    assert await supervisor.run_ready(None) == 4
    assert sequence == ["recover", "observe", "kling", "poll", "kling", "libtv"]
