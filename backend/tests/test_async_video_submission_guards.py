"""Async AI video submission guards: no stranded uploads or false recoveries."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask
from libtv_agent.services.async_video_submission import (
    accept_automatic_video_submission,
    mark_interrupted_automatic_plans,
    pending_automatic_video_ids,
    release_stopped_automatic_plan,
    heartbeat_automatic_video_plan,
    PAYLOAD_KEY,
)
from libtv_agent.services.fixed_video_tasks import FixedVideoTaskValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_missing_ai_key_rejected_before_creating_or_claiming_task():
    settings = Settings(
        ai_adapter_mode="openai", openai_api_key=None, _env_file=None,
    )
    with pytest.raises(FixedVideoTaskValidationError) as error:
        await accept_automatic_video_submission(
            None,
            user_id=uuid.uuid4(),
            payload=SimpleNamespace(paid_submission_consent=True, video_type="ootd"),
            settings=settings,
        )
    assert error.value.code == "AI_CREATIVE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_live_ai_plan_is_not_mistaken_for_abandoned_plan(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'state.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        now = datetime.now(UTC) - timedelta(minutes=20)
        active_id, abandoned_id = uuid.uuid4(), uuid.uuid4()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for identifier in (active_id, abandoned_id):
                session.add(ParentTask(
                    id=identifier,
                    user_id=uuid.uuid4(),
                    client_task_id=f"recovery-{identifier}",
                    title="AI plan",
                    asset_count=1,
                    phase=ParentTaskPhase.ANALYZING,
                    workflow_kind="FIXED_TEMPLATE",
                    analysis_stage="RUNNING",
                    analysis_started_at=now,
                ))
            await session.commit()
        async with factory() as session:
            count = await mark_interrupted_automatic_plans(
                session, exclude_ids=(active_id,),
            )
            assert count == 1
        async with factory() as session:
            assert (await session.get(ParentTask, active_id)).phase == ParentTaskPhase.ANALYZING
            abandoned = await session.get(ParentTask, abandoned_id)
            assert abandoned.phase == ParentTaskPhase.ACTION_REQUIRED
            assert abandoned.phase_reason_code == "AI_CREATIVE_INTERRUPTED"
    finally:
        await engine.dispose()
