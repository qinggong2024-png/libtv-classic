"""Production dashboard separates executable work from historical remediation records."""
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from libtv_agent.config import Settings
from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase, ReservationStatus, VariantKind
from libtv_agent.domain.models import (
    Base,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    PointsReservation,
    RunnerHost,
    RuntimeConfigVersion,
    User,
)
from libtv_agent.services.observability import operations_snapshot
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _open_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_dashboard_counts_blocked_attempts_and_uncertain_reservations_separately(
    tmp_path: Path,
) -> None:
    engine, factory = await _open_db()
    now = datetime.now(UTC)
    async with factory() as session:
        user = User(username="operations-health", password_hash="unused")
        session.add(user)
        await session.flush()

        async def attempt(phase, reason, status):
            task = ParentTask(
                user_id=user.id,
                client_task_id=str(uuid.uuid4()),
                title="metrics fixture",
                asset_count=1,
                phase=phase,
                phase_reason_code=reason,
            )
            session.add(task)
            await session.flush()
            variant = GenerationVariant(
                parent_task_id=task.id,
                variant_type=VariantKind.STABLE,
                generation_config_json={},
            )
            session.add(variant)
            await session.flush()
            row = GenerationAttempt(
                variant_id=variant.id,
                status=status,
                updated_at=now - timedelta(minutes=4),
            )
            session.add(row)
            await session.flush()
            return row

        for _ in range(2):
            await attempt(
                ParentTaskPhase.ACTION_REQUIRED, "USD_DAILY_BUDGET_EXCEEDED", AttemptStatus.PREPARED
            )
        await attempt(
            ParentTaskPhase.ACTION_REQUIRED, "POINTS_LIMIT_EXCEEDED", AttemptStatus.PREPARED
        )
        await attempt(ParentTaskPhase.DELIVERABLE, None, AttemptStatus.PREPARED)
        await attempt(ParentTaskPhase.QUEUED, None, AttemptStatus.PREPARED)
        succeeded = await attempt(ParentTaskPhase.DELIVERABLE, None, AttemptStatus.SUCCEEDED)
        failed = await attempt(ParentTaskPhase.ACTION_REQUIRED, None, AttemptStatus.FAILED_TERMINAL)
        unknown = await attempt(
            ParentTaskPhase.ACTION_REQUIRED, None, AttemptStatus.UNKNOWN_AFTER_SUBMIT
        )
        for linked in (succeeded, failed, unknown):
            session.add(
                PointsReservation(
                    user_id=user.id,
                    attempt_id=linked.id,
                    estimated_points=1,
                    status=ReservationStatus.UNCERTAIN,
                    business_date=now.date(),
                )
            )
        session.add_all(
            [
                RunnerHost(
                    hostname="healthy-runner", service_instance_id="healthy",
                    os_version="windows", service_version="test",
                    last_heartbeat_at=now,
                ),
                RunnerHost(
                    hostname="stale-runner", service_instance_id="stale",
                    os_version="windows", service_version="test",
                    last_heartbeat_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.add(RuntimeConfigVersion(
            version=1,
            config_json={
                "globalPaused": False,
                "paidSubmissionsPaused": False,
                "generationConcurrency": {"global": 1},
                "preparedQueue": {"lowWatermark": 0, "highWatermark": 24},
            },
            change_reason="test",
        ))
        await session.commit()
        snapshot = await operations_snapshot(
            session,
            Settings(object_store_mode="local", runner_root=tmp_path / "runner", _env_file=None),
        )

    queue = snapshot["productionHealth"]["queue"]
    assert snapshot["queues"]["prepared"] == 5  # legacy-compatible raw count
    assert snapshot["queues"]["actionablePrepared"] == 1
    assert snapshot["queues"]["blockedPrepared"] == 4
    assert queue["actionableTotal"] == 1
    assert queue["blockedBudgetUsd"] == 2
    assert queue["blockedLegacyLimit"] == 1
    assert queue["blockedOther"] == 1
    assert queue["oldestActionableWaitSeconds"] is not None
    assert queue["oldestActionableWaitSeconds"] >= 230
    assert snapshot["metrics"]["runnerOnlineHosts"] == 1
    assert snapshot["metrics"]["runnerActiveCommandHosts"] == 0
    assert snapshot["productionHealth"]["cloudUnknownAttempts"] == 1
    assert snapshot["productionHealth"]["reservationsAwaitingReview"] == 3
    assert snapshot["productionHealth"]["reservationOutcomes"] == {
        "succeeded": 1, "failed": 1, "cloudUnknown": 1, "other": 0,
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_capacity_without_executable_backlog_does_not_claim_queue_blockage(
    tmp_path: Path,
) -> None:
    engine, factory = await _open_db()
    async with factory() as session:
        user = User(username="no-backlog", password_hash="unused")
        session.add(user)
        await session.flush()
        task = ParentTask(
            user_id=user.id, client_task_id="no-backlog",
            title="running", asset_count=1, phase=ParentTaskPhase.GENERATING,
        )
        session.add(task)
        await session.flush()
        variant = GenerationVariant(
            parent_task_id=task.id, variant_type=VariantKind.STABLE,
            generation_config_json={},
        )
        session.add(variant)
        await session.flush()
        session.add(GenerationAttempt(variant_id=variant.id, status=AttemptStatus.RUNNING))
        session.add(RuntimeConfigVersion(
            version=1,
            config_json={
                "globalPaused": False,
                "paidSubmissionsPaused": False,
                "generationConcurrency": {"global": 1},
                "preparedQueue": {"lowWatermark": 0, "highWatermark": 24},
            },
            change_reason="test",
        ))
        await session.commit()
        snapshot = await operations_snapshot(
            session,
            Settings(object_store_mode="local", runner_root=tmp_path / "runner", _env_file=None),
        )
    assert snapshot["queues"]["running"] == 1
    assert snapshot["queues"]["actionableTotal"] == 0
    assert snapshot["pipelinePerformance"]["currentConstraint"] != "GENERATION_CAPACITY"
    await engine.dispose()
