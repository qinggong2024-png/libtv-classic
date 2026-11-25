"""Isolated Runner write suppression and receipt-first recovery regressions."""

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.video_provider import ProviderOutput, ProviderTaskResult
from libtv_agent.domain.enums import (
    AttemptStatus, CommandStatus, DownloadStatus, GenerationStatus,
    LedgerEntryType, ReservationStatus,
)
from libtv_agent.domain.models import (
    DownloadArtifact, GenerationAttempt, GenerationCommand, GenerationVariant,
    PointsLedger, PointsReservation,
)
from libtv_agent.runner.supervisor import RunnerSupervisor
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_success_path_settlement import _world, _settings, _result, session  # noqa: F401


async def test_stable_poll_is_read_only_until_next_checkpoint(session, tmp_path):
    settings = _settings(tmp_path).model_copy(update={
        "kling_api_enabled": True, "kling_provider_backend": "api",
        "kling_api_key": "synthetic-offline-test-key",
        "classic_throughput_s1s3_enabled": True,
        "kling_api_poll_interval_seconds": 5,
    })
    task, variant, attempt, command, _ = await _world(
        session, provider=settings.kling_api_provider_name,
    )
    now = datetime.now(UTC)
    attempt.status = AttemptStatus.RUNNING
    attempt.task_id = "paid-handle"
    attempt.provider_status = "running"
    attempt.provider_raw_status = "RUNNING"
    attempt.raw_response_json = {"status": "RUNNING"}
    attempt.last_polled_at = now - timedelta(seconds=10)
    attempt.submitted_at = now - timedelta(seconds=30)
    command.status = CommandStatus.PROCESS_STARTED
    command.lease_expires_at = None
    variant.generation_status = GenerationStatus.RUNNING
    await session.commit()
    aid, vid, original_version = attempt.id, variant.id, attempt.state_version
    query = AsyncMock(return_value=ProviderTaskResult(
        "running", "RUNNING", (), {"status": "RUNNING"},
    ))
    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[settings.kling_api_provider_name] = SimpleNamespace(query=query)
    writes = []
    def observe(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE ")):
            writes.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", observe)
    try:
        assert await supervisor.poll_kling_tasks(session) == 1
        assert writes == []
        assert (await session.get(GenerationAttempt, aid)).state_version == original_version
        assert await supervisor.poll_kling_tasks(session) == 0
        query.assert_awaited_once_with("paid-handle")
        # Once the normal interval is due, a terminal observation must take
        # the ordinary state-version CAS and persist the existing paid handle.
        version, handle, _ = supervisor._unchanged_poll_observations[aid]
        supervisor._unchanged_poll_observations[aid] = (version, handle, now - timedelta(seconds=10))
        query.return_value = ProviderTaskResult(
            "succeeded", "COMPLETED", (ProviderOutput("https://synthetic.invalid/video.mp4"),),
            {"status": "COMPLETED"}, credits_consumed=120,
        )
        assert await supervisor.poll_kling_tasks(session) == 1
        assert writes
        fresh = await session.get(GenerationAttempt, aid)
        assert fresh.status == AttemptStatus.SUCCEEDED
        assert fresh.task_id == "paid-handle"
        assert (await session.get(GenerationVariant, vid)).download_status == DownloadStatus.QUEUED
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", observe)


async def test_download_record_lock_keeps_receipt_and_settlement_recoverable(session, tmp_path, monkeypatch):
    task, variant, attempt, command, _ = await _world(session)
    aid, vid, cid = attempt.id, variant.id, command.id
    download = AsyncMock()
    monkeypatch.setattr("libtv_agent.runner.supervisor.download_with_retries", download)
    monkeypatch.setattr("libtv_agent.runner.supervisor.technical_validate", AsyncMock(
        return_value=SimpleNamespace(sha256="a"*64, size_bytes=1024, duration_seconds=5,
                                     width=1080, height=1920, has_audio=False,
                                     passed=True, issue_codes=()),
    ))
    supervisor = RunnerSupervisor(object(), SimpleNamespace(put_file=AsyncMock(
        return_value="synthetic/output.mp4")), _settings(tmp_path))
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO download_artifact "):
            raise OperationalError(statement, parameters, sqlite3.OperationalError("database is locked"))
    event.listen(session.get_bind(), "before_cursor_execute", fail)
    try:
        with pytest.raises(OperationalError, match="database is locked"):
            await supervisor._handle_success(session, command, attempt, _result())
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", fail)
    # No PendingRollbackError, and no second provider submission.
    assert await session.scalar(select(1)) == 1
    fresh = await session.get(GenerationAttempt, aid)
    assert fresh.task_id == "provider-task"
    assert fresh.status == AttemptStatus.SUCCEEDED
    assert (await session.get(GenerationVariant, vid)).download_status == DownloadStatus.DOWNLOADING
    assert (await session.get(GenerationCommand, cid)).status == CommandStatus.TERMINAL
    assert (await session.scalar(select(PointsReservation).where(
        PointsReservation.attempt_id == aid))).status == ReservationStatus.SETTLED
    assert await session.scalar(select(func.count()).select_from(DownloadArtifact)) == 0
    assert await supervisor.resume_incomplete_downloads(session) == 1
    assert (await session.get(GenerationVariant, vid)).download_status == DownloadStatus.READY
    assert await session.scalar(select(func.count()).select_from(DownloadArtifact)) == 1
    assert await session.scalar(select(func.count()).select_from(PointsLedger).where(
        PointsLedger.attempt_id == aid, PointsLedger.entry_type == LedgerEntryType.CONSUME)) == 1

def test_runner_heartbeat_cadence_preserves_immediate_safety_changes():
    from libtv_agent.domain.enums import RunnerHostStatus
    from libtv_agent.runner.service_runtime import _runner_heartbeat_due

    now = datetime.now(UTC)
    host = SimpleNamespace(last_heartbeat_at=now.replace(tzinfo=None),
                           status=RunnerHostStatus.ONLINE, free_disk_percent=40.0)
    def due(seconds, status=RunnerHostStatus.ONLINE, free=40.0, orphan=False):
        return _runner_heartbeat_due(host, status=status, free_percent=free,
                                     observed_at=now + timedelta(seconds=seconds),
                                     orphan_changed=orphan)
    assert not due(1)
    assert not due(14, free=39.5)
    assert due(15)
    assert due(1, status=RunnerHostStatus.PAUSED)
    assert due(1, status=RunnerHostStatus.WARNING)
    assert due(1, free=38.0)
    assert due(1, orphan=True)
    assert due(-1)
