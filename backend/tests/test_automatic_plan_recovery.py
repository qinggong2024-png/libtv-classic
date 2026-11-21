"""Crash-safe recovery of pre-paid Sol planning: no extra Kling submissions."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from libtv_agent import config as config_module
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase, VariantKind
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask
from libtv_agent.services.async_video_submission import (
    PAYLOAD_KEY,
    heartbeat_automatic_video_plan,
    mark_interrupted_automatic_plans,
    pending_automatic_video_ids,
    release_stopped_automatic_plan,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]


async def make_factory(tmp_path, monkeypatch, *, migrated=False):
    path = tmp_path / "recovery.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    if migrated:
        settings = Settings(database_url=url, app_env="test", _env_file=None)
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)
        await asyncio.to_thread(command.upgrade, Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    engine = create_async_engine(url)
    if not migrated:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def planned_task(
    *, heartbeat_age_minutes=20, attempt_count=1, with_payload=True,
    run_id=None,
):
    old = datetime.now(UTC) - timedelta(minutes=heartbeat_age_minutes)
    return ParentTask(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        client_task_id=f"prepaid-{uuid.uuid4()}",
        title="recovery fixture",
        asset_count=1,
        workflow_kind="FIXED_TEMPLATE",
        phase=ParentTaskPhase.ANALYZING,
        analysis_stage="RUNNING",
        analysis_run_id=run_id or uuid.uuid4(),
        analysis_started_at=old,
        analysis_heartbeat_at=old,
        analysis_attempt_count=attempt_count,
        created_at=old,
        updated_at=old,
        settings_json={PAYLOAD_KEY: {"generationMode": "automatic"}} if with_payload else {},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("migrated", [False, True])
async def test_abandoned_prepaid_plan_requeued_once_on_real_and_model_schema(
    tmp_path, monkeypatch, migrated,
):
    engine, factory = await make_factory(tmp_path, monkeypatch, migrated=migrated)
    try:
        task = planned_task()
        old_id = task.analysis_run_id
        async with factory() as session:
            session.add(task)
            await session.commit()
        async with factory() as session:
            assert await mark_interrupted_automatic_plans(session) == 1
            assert await mark_interrupted_automatic_plans(session) == 0
            assert task.id in await pending_automatic_video_ids(session)
        async with factory() as session:
            refreshed = await session.get(ParentTask, task.id)
            assert refreshed.phase == ParentTaskPhase.ANALYZING
            assert refreshed.analysis_stage == "QUEUED"
            assert refreshed.analysis_run_id is None
            assert refreshed.analysis_attempt_count == 1
            assert await release_stopped_automatic_plan(
                session, task_id=task.id, run_id=old_id,
            ) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_variant_or_exhausted_retries_require_manual_review(tmp_path, monkeypatch):
    engine, factory = await make_factory(tmp_path, monkeypatch)
    try:
        with_variant = planned_task()
        too_many_attempts = planned_task(attempt_count=2)
        missing_request = planned_task(with_payload=False)
        async with factory() as session:
            session.add_all([with_variant, too_many_attempts, missing_request])
            session.add(GenerationVariant(
                parent_task_id=with_variant.id, variant_type=VariantKind.STABLE,
                revision=1,
            ))
            await session.commit()
        async with factory() as session:
            assert await mark_interrupted_automatic_plans(session) == 3
            assert await pending_automatic_video_ids(session) == []
        async with factory() as session:
            for candidate in (with_variant, too_many_attempts, missing_request):
                persisted = await session.get(ParentTask, candidate.id)
                assert persisted.phase == ParentTaskPhase.ACTION_REQUIRED
                assert persisted.phase_reason_code == "AI_CREATIVE_INTERRUPTED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_heartbeat_prevents_false_recovery_and_old_owner_cannot_release(
    tmp_path, monkeypatch,
):
    engine, factory = await make_factory(tmp_path, monkeypatch)
    try:
        task = planned_task()
        async with factory() as session:
            session.add(task)
            await session.commit()
        database = type("DatabaseStub", (), {"session_factory": factory})()
        heartbeat = asyncio.create_task(heartbeat_automatic_video_plan(
            database, task.id, task.analysis_run_id, interval_seconds=0.02,
        ))
        try:
            await asyncio.sleep(0.09)
            async with factory() as session:
                assert await mark_interrupted_automatic_plans(session) == 0
                assert (await session.get(ParentTask, task.id)).analysis_stage == "RUNNING"
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        async with factory() as session:
            assert await release_stopped_automatic_plan(
                session, task_id=task.id, run_id=task.analysis_run_id,
            )
            assert (await session.get(ParentTask, task.id)).analysis_stage == "QUEUED"
            assert await release_stopped_automatic_plan(
                session, task_id=task.id, run_id=task.analysis_run_id,
            ) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_or_nonplanning_tasks_are_untouched(tmp_path, monkeypatch):
    engine, factory = await make_factory(tmp_path, monkeypatch)
    try:
        live = planned_task()
        not_in_planning = planned_task()
        not_in_planning.phase = ParentTaskPhase.PREPARING
        async with factory() as session:
            session.add_all([live, not_in_planning])
            await session.commit()
        async with factory() as session:
            assert await mark_interrupted_automatic_plans(
                session, exclude_ids=(live.id,),
            ) == 0
        async with factory() as session:
            assert (await session.get(ParentTask, live.id)).analysis_stage == "RUNNING"
            assert (
                await session.get(ParentTask, not_in_planning.id)
            ).phase == ParentTaskPhase.PREPARING
    finally:
        await engine.dispose()
