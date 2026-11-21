"""A finished pair of raw clips must never remain a generating parent."""

from datetime import UTC, datetime, timedelta

import pytest
from libtv_agent.domain.enums import (
    DownloadStatus,
    GenerationStatus,
    ParentTaskPhase,
    ValidationStatus,
    VariantKind,
)
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask, User
from libtv_agent.runner.supervisor import RunnerSupervisor
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class NoProvider:
    def __getattr__(self, name):
        raise AssertionError(f"reconciliation must not call any provider: {name}")


@pytest.mark.asyncio
async def test_reconcile_only_fully_validated_stale_generating_parents(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stale.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(username="stale-state", password_hash="disabled")
        session.add(user)
        await session.flush()
        ids = []
        for index, completed in enumerate((True, False)):
            parent = ParentTask(
                user_id=user.id, client_task_id=f"state-{index}", title="state",
                asset_count=2, phase=ParentTaskPhase.GENERATING,
                updated_at=datetime.now(UTC) - timedelta(minutes=5),
            )
            session.add(parent)
            await session.flush()
            ids.append(parent.id)
            for slot in range(2):
                session.add(GenerationVariant(
                    parent_task_id=parent.id,
                    variant_type=VariantKind.STABLE,
                    revision=slot + 1,
                    variant_key=f"result-{slot}",
                    generation_config_json={},
                    generation_status=GenerationStatus.SUCCEEDED,
                    download_status=DownloadStatus.READY,
                    validation_status=(
                        ValidationStatus.PASSED
                        if completed or slot == 0 else ValidationStatus.FAILED
                    ),
                ))
        await session.commit()
        # Bypass constructor: this recovery uses only database receipts.
        supervisor = object.__new__(RunnerSupervisor)
        assert await supervisor.reconcile_completed_parent_phases(session) == 1
        session.expire_all()
        assert (await session.get(ParentTask, ids[0])).phase == ParentTaskPhase.DELIVERABLE
        assert (await session.get(ParentTask, ids[1])).phase == ParentTaskPhase.GENERATING
        assert await supervisor.reconcile_completed_parent_phases(session) == 0
    await engine.dispose()
