import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from libtv_agent.api import tasks as tasks_api
from libtv_agent.app import create_app
from libtv_agent.db import Database
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import GenerationCommand, ParentTask
from libtv_agent.services.analysis_lifecycle import (
    DURABLE_ANALYSIS_QUEUED,
    StaleAnalysisRunError,
    recover_stale_analyses,
    touch_analysis_run,
)
from libtv_agent.services.durable_analysis import claim_next, worker_loop
from sqlalchemy import select, update
from test_workflow_integration import create_draft_task, running_app, user_headers

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"


async def enqueue(client, name):
    task = await create_draft_task(client, name)
    payload = {"expectedStateVersion": task["stateVersion"]}
    url = f"/api/v1/tasks/{task['id']}/analyze"
    response = await client.post(url, headers=user_headers("analyze-" + name), json=payload)
    assert response.status_code == 202, response.text
    return UUID(task["id"]), response.json(), url, payload


@pytest.mark.asyncio
async def test_durable_api_exit_replay_and_real_fake_analysis(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, accepted, url, payload = await enqueue(client, "durable-api")
        assert accepted["analysisStage"] == DURABLE_ANALYSIS_QUEUED
        assert not app.state.analysis_jobs
        replay = await client.post(url, headers=user_headers("analyze-durable-api"), json=payload)
        assert replay.status_code == 202
        assert replay.json() == accepted
        settings = app.state.settings
    # The first API has completely shut down; use a fresh database connection.
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            claim = await claim_next(session, stale_seconds=660)
            assert claim and claim[0] == task_id
        worker_app = create_app(settings)
        worker_app.state.database = database
        await tasks_api._run_analysis_job(worker_app, *claim)
        async with database.session_factory() as session:
            task = await session.get(ParentTask, task_id)
            assert task.phase == ParentTaskPhase.AWAITING_CONFIRMATION, task.phase_reason_code
            assert task.analysis_result_json
            assert task.analysis_attempt_count == 1
            assert not list(await session.scalars(select(GenerationCommand)))
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_claim_and_stale_fence(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, *_ = await enqueue(client, "durable-race")
        db = app.state.database
        now = datetime.now(UTC)

        async def claim(at):
            async with db.session_factory() as session:
                return await claim_next(session, stale_seconds=660, now=at)

        results = await asyncio.gather(claim(now), claim(now))
        winners = [item for item in results if item]
        assert len(winners) == 1
        old = winners[0]
        assert old[0] == task_id
        assert await claim(now + timedelta(seconds=5)) is None
        later = now + timedelta(seconds=661)
        replacement = await claim(later)
        assert replacement and replacement[0] == task_id and replacement[2] != old[2]
        async with db.session_factory() as session:
            with pytest.raises(StaleAnalysisRunError):
                await touch_analysis_run(session, task_id, old[2], "PROVIDER_RUNNING")
        async with db.session_factory() as session:
            await touch_analysis_run(session, task_id, replacement[2], "PROVIDER_RUNNING")
            await session.execute(
                update(ParentTask)
                .where(ParentTask.id == task_id)
                .values(analysis_heartbeat_at=now - timedelta(seconds=1000))
            )
            await session.commit()
        assert await claim(later) is None
        async with db.session_factory() as session:
            recovered = await recover_stale_analyses(session, app.state.settings, now=later)
            assert task_id in recovered
            task = await session.get(ParentTask, task_id)
            assert task.phase == ParentTaskPhase.ACTION_REQUIRED
        assert await claim(later) is None


@pytest.mark.asyncio
async def test_old_queued_and_new_queued_remain_separate(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, *_ = await enqueue(client, "durable-queue")
        now = datetime.now(UTC)
        async with app.state.database.session_factory() as session:
            await session.execute(
                update(ParentTask)
                .where(ParentTask.id == task_id)
                .values(analysis_heartbeat_at=now - timedelta(seconds=1000))
            )
            await session.commit()
            assert task_id not in await recover_stale_analyses(session, app.state.settings, now=now)
            assert (
                await session.get(ParentTask, task_id)
            ).analysis_stage == DURABLE_ANALYSIS_QUEUED
            await session.execute(
                update(ParentTask).where(ParentTask.id == task_id).values(analysis_stage="QUEUED")
            )
            await session.commit()
            assert await claim_next(session, stale_seconds=660) is None


@pytest.mark.asyncio
async def test_two_worker_loops_process_distinct_jobs(tmp_path, monkeypatch):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        first, *_ = await enqueue(client, "parallel-a")
        second, *_ = await enqueue(client, "parallel-b")
        entered = asyncio.Event()
        release = asyncio.Event()
        stop = Event()
        seen = []

        async def fake_run(worker_app, task_id, user_id, run_id):
            seen.append(task_id)
            if len(seen) == 2:
                entered.set()
            await release.wait()

        monkeypatch.setattr(tasks_api, "_run_analysis_job", fake_run)
        workers = [
            asyncio.create_task(
                worker_loop(
                    app.state.database,
                    app.state.settings,
                    app.state.object_store,
                    app.state.kling_adapter,
                    stop,
                )
            )
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert set(seen) == {first, second}
        finally:
            stop.set()
            release.set()
            await asyncio.gather(*workers)


@pytest.mark.asyncio
async def test_graceful_cancel_unstarted_claim_requeues(tmp_path, monkeypatch):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, *_ = await enqueue(client, "cancel-before-entry")
        async with app.state.database.session_factory() as session:
            claim = await claim_next(session, stale_seconds=660)
        entered = asyncio.Event()

        async def block(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(tasks_api, "analyze_task", block)
        job = asyncio.create_task(tasks_api._run_analysis_job(app, *claim))
        await asyncio.wait_for(entered.wait(), 5)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        async with app.state.database.session_factory() as session:
            row = await session.get(ParentTask, task_id)
            assert row.analysis_stage == DURABLE_ANALYSIS_QUEUED
            assert row.analysis_run_id != claim[2]
            assert row.phase == ParentTaskPhase.ANALYZING
            next_claim = await claim_next(session, stale_seconds=660)
            assert next_claim and next_claim[0] == task_id


@pytest.mark.asyncio
async def test_stale_worker_cannot_start_provider(tmp_path):
    from libtv_agent.services.analysis import _await_provider_with_heartbeat

    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, *_ = await enqueue(client, "provider-fence")
        now = datetime.now(UTC)
        async with app.state.database.session_factory() as session:
            old = await claim_next(session, stale_seconds=660, now=now)
        async with app.state.database.session_factory() as session:
            new = await claim_next(session, stale_seconds=660, now=now + timedelta(seconds=661))
        assert old and new and old[2] != new[2]

        class NeverCalled:
            async def analyze(self, *args):
                pytest.fail("stale execution called provider")

        async with app.state.database.session_factory() as session:
            with pytest.raises(StaleAnalysisRunError):
                await _await_provider_with_heartbeat(
                    session, task_id, old[2], NeverCalled(), "", []
                )


@pytest.mark.asyncio
async def test_acceptance_transaction_rolls_back_if_idempotent_receipt_fails(tmp_path, monkeypatch):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        draft = await create_draft_task(client, "atomic-accept")
        url = f"/api/v1/tasks/{draft['id']}/analyze"
        payload = {"expectedStateVersion": draft["stateVersion"]}
        real_store = tasks_api.store_idempotent_response

        async def fail_store(*args, **kwargs):
            raise RuntimeError("injected before durable acceptance commit")

        monkeypatch.setattr(tasks_api, "store_idempotent_response", fail_store)
        with pytest.raises(RuntimeError, match="injected"):
            await client.post(url, headers=user_headers("atomic-accept"), json=payload)
        async with app.state.database.session_factory() as session:
            row = await session.get(ParentTask, UUID(draft["id"]))
            assert row.phase == ParentTaskPhase.DRAFT
            assert await claim_next(session, stale_seconds=660) is None
        monkeypatch.setattr(tasks_api, "store_idempotent_response", real_store)
        result = await client.post(url, headers=user_headers("atomic-accept"), json=payload)
        assert result.status_code == 202
        assert result.json()["analysisStage"] == DURABLE_ANALYSIS_QUEUED


@pytest.mark.asyncio
@pytest.mark.parametrize("after_dispatch", [False, True])
async def test_process_crash_recovery_boundary(tmp_path, after_dispatch):
    import os
    import subprocess
    import sys

    child = """
import asyncio
import os
import sys
from libtv_agent.db import Database
from libtv_agent.services.durable_analysis import claim_next
from libtv_agent.services.analysis_lifecycle import touch_analysis_run

async def main():
    database = Database(sys.argv[1])
    async with database.session_factory() as session:
        claim = await claim_next(session, stale_seconds=660)
        assert claim
        if sys.argv[2] == "True":
            await touch_analysis_run(session, claim[0], claim[2], "PROVIDER_RUNNING")
    # Deliberately bypass normal teardown and cancellation cleanup.
    os._exit(17)

asyncio.run(main())
"""
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_analysis_enabled = True
        task_id, *_ = await enqueue(client, "process-crash")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_SRC)
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", child, app.state.settings.database_url, str(after_dispatch)],
            env=env,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 17, result.stderr.decode(errors="replace")
        db = app.state.database
        later = datetime.now(UTC) + timedelta(seconds=661)
        async with db.session_factory() as session:
            row = await session.get(ParentTask, task_id)
            old_id = row.analysis_run_id
            if after_dispatch:
                assert row.analysis_stage == "PROVIDER_RUNNING"
            else:
                assert row.analysis_stage == "DURABLE_CLAIMED"
        async with db.session_factory() as session:
            new = await claim_next(session, stale_seconds=660, now=later)
            if after_dispatch:
                assert new is None
                assert task_id in await recover_stale_analyses(
                    session, app.state.settings, now=later
                )
            else:
                assert new and new[0] == task_id and new[2] != old_id
