"""Real OS crash + independent interpreter validates durable Sol lease takeover."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import ParentTask
from libtv_agent.services.async_video_submission import mark_interrupted_automatic_plans
from libtv_agent.services.sol_plan_checkpoint import restored_plan, save_plan_checkpoint
from sqlalchemy import update
from test_automatic_plan_recovery import make_factory, planned_task
from test_sol_plan_checkpoint import fixture_variant

ROOT = Path(__file__).resolve().parents[2]
CHILD = ROOT / "scripts" / "classic_v2_process_crash_child.py"


@pytest.mark.asyncio
async def test_prepaid_checkpoint_survives_real_process_death_and_new_lease(
    tmp_path, monkeypatch,
):
    engine, factory = await make_factory(tmp_path, monkeypatch)
    try:
        original = planned_task(heartbeat_age_minutes=1)
        task_id = original.id
        old_run_id = original.analysis_run_id
        assert old_run_id is not None
        async with factory() as session:
            session.add(original)
            await session.commit()

        child_env = {
            **os.environ,
            "ALLWE_V2_FAULT_INJECTION": "LOCAL_ONLY",
            "PYTHONPATH": str(ROOT / "backend" / "src"),
        }
        # Run a separate Python interpreter that commits a valid Sol plan and
        # then calls os._exit(79). Parent does NOT share child's DB connection.
        child = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable, str(CHILD),
                "--database", str(tmp_path / "recovery.db"),
                "--task-id", str(task_id),
                "--run-id", str(old_run_id),
                "--fingerprint", "one-immutable-offline-request",
            ],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        assert child.returncode == 79, (
            f"expected deliberate OS process termination, got {child.returncode}: "
            f"{child.stderr[:400]}"
        )

        async with factory() as session:
            after_crash = await session.get(ParentTask, task_id)
            assert after_crash.phase == ParentTaskPhase.ANALYZING
            assert after_crash.analysis_stage == "RUNNING"
            cached = restored_plan(
                after_crash, fingerprint="one-immutable-offline-request",
                variant_count=1,
            )
            assert cached is not None
            assert cached[0].planner_version == "offline-process-fault-injection-v1"
            assert await mark_interrupted_automatic_plans(
                session, older_than_minutes=0,
            ) == 1
        async with factory() as session:
            new_run_id = uuid.uuid4()
            claimed = await session.execute(
                update(ParentTask).where(
                    ParentTask.id == task_id,
                    ParentTask.phase == ParentTaskPhase.ANALYZING,
                    ParentTask.analysis_stage == "QUEUED",
                ).values(
                    analysis_stage="RUNNING",
                    analysis_run_id=new_run_id,
                    analysis_started_at=datetime.now(UTC),
                    analysis_heartbeat_at=datetime.now(UTC),
                    analysis_attempt_count=ParentTask.analysis_attempt_count + 1,
                )
            )
            assert claimed.rowcount == 1
            await session.commit()

        async with factory() as session:
            assert not await save_plan_checkpoint(
                session, task_id=task_id, run_id=old_run_id,
                fingerprint="one-immutable-offline-request",
                variants=[fixture_variant()],
            ), "dead worker must not write after a new lease owns the task"
        async with factory() as session:
            recovered = await session.get(ParentTask, task_id)
            assert recovered.analysis_run_id == new_run_id
            assert recovered.analysis_attempt_count == 2
            assert restored_plan(
                recovered, fingerprint="one-immutable-offline-request",
                variant_count=1,
            ) == cached, "new worker sees exactly the committed plan, no Sol repeat"
            assert recovered.variants == [], "crash and recovery never paid Kling"
    finally:
        await engine.dispose()
