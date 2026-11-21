"""CLI task uncertainty must not strand unrelated work or retry paid requests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.config import Settings
from libtv_agent.domain.enums import AttemptStatus, CommandStatus, GenerationStatus
from libtv_agent.domain.models import Base, GenerationAttempt, GenerationCommand, GenerationVariant
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services import runtime_control
from libtv_agent.services.runtime_control import (
    RuntimeControlBlockedError,
    UnknownSubmissionRisk,
    assess_unknown_submission_risk,
    ensure_provider_submission_allowed,
    provider_claims_allowed,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_launch_started_recovery import _stranded_command
from test_runtime_control import USER_ID, _risk_attempt


@pytest.mark.asyncio
async def test_cli_timeout_keeps_original_handle_and_only_queries_it(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'timeout.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        app_env="test", kling_cli_enabled=True,
        object_store_mode="local", _env_file=None,
    )
    async with factory() as session:
        _, variant, attempt, command, _ = await _stranded_command(
            session, provider=settings.kling_cli_provider_name, lease_expires_at=None,
        )
        attempt.status = AttemptStatus.RUNNING
        attempt.task_id = "generation-original-123"
        attempt.submitted_at = datetime.now(UTC) - timedelta(days=30)
        command.status = CommandStatus.PROCESS_STARTED
        await session.commit()
        attempt_id, command_id, variant_id = attempt.id, command.id, variant.id

    adapter = SimpleNamespace(
        query=AsyncMock(return_value=SimpleNamespace(
            status="running", raw_status="processing", raw_response={"status": "processing"},
            credits_consumed=None, outputs=(),
        )),
        submit=AsyncMock(side_effect=AssertionError("paid duplicate submission")),
    )
    supervisor = RunnerSupervisor(object(), object(), settings)
    supervisor._kling_adapters[settings.kling_cli_provider_name] = adapter

    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        variant = await session.get(GenerationVariant, variant_id)
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.task_id == "generation-original-123"
        assert attempt.next_reconciliation_at is not None
        assert command.status == CommandStatus.UNCERTAIN
        assert variant.generation_status == GenerationStatus.UNKNOWN
        attempt.next_reconciliation_at = datetime.now(UTC) - timedelta(seconds=1)
        attempt.last_polled_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
    adapter.query.assert_awaited_once_with("generation-original-123")
    adapter.submit.assert_not_awaited()
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == "generation-original-123"
    await engine.dispose()


@pytest.mark.asyncio
async def test_cli_user_unknown_does_not_block_new_independent_task(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cli-risk.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        kling_cli_enabled=True, paid_submissions_paused=False,
        object_store_mode="local", _env_file=None,
    )
    now = datetime.now(UTC)
    async def runtime_config(_session, _settings):
        return SimpleNamespace(config_json={
            "globalPaused": False, "paidSubmissionsPaused": False,
        })
    monkeypatch.setattr(runtime_control, "real_provider_submission_authorized", lambda _: True)
    monkeypatch.setattr(runtime_control, "get_runtime_config", runtime_config)

    async with factory() as session:
        session.add_all([
            _risk_attempt(
                900 + i, user_id=USER_ID, provider=settings.kling_cli_provider_name,
                status=AttemptStatus.UNKNOWN_AFTER_SUBMIT, event_at=now,
            ) for i in range(5)
        ])
        await session.commit()
        risk = await assess_unknown_submission_risk(
            session, settings, settings.kling_cli_provider_name, user_id=USER_ID, now=now,
        )
        assert risk.block_user and not risk.block_provider
        await ensure_provider_submission_allowed(
            session, settings, settings.kling_cli_provider_name, user_id=USER_ID,
        )
        assert await provider_claims_allowed(
            session, settings, settings.kling_cli_provider_name, user_id=USER_ID,
        )
    await engine.dispose()


def test_cli_still_blocks_multi_user_provider_wide_incident():
    risk = UnknownSubmissionRisk(
        block_provider=True, block_user=True, provider_attempt_count=6,
        provider_unknown_task_count=5, provider_unknown_user_count=3,
        user_unknown_task_count=5,
    )
    with pytest.raises(RuntimeControlBlockedError) as captured:
        runtime_control._raise_for_unknown_risk(risk, isolate_cli_user=True)
    assert captured.value.code == "PROVIDER_UNKNOWN_CIRCUIT_OPEN"
