import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from libtv_agent.db import Database
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import GenerationCommand, OutboxEvent, ParentTask
from libtv_agent.services.durable_confirmation import (
    FINISHED,
    QUEUED,
    REQUESTED,
    REVIEW,
    RUNNING,
    claim_confirmation,
    execute_confirmation,
    reconcile_abandoned_confirmations,
)
from libtv_agent.services.materialization_lifecycle import recover_stale_materializations
from sqlalchemy import select, update
from test_workflow_integration import create_analyzed_task, running_app, user_headers


async def enqueue(client, name):
    task = await create_analyzed_task(client, client_task_id=name)
    url = f"/api/v1/tasks/{task['id']}/confirm"
    payload = {"expectedStateVersion": task["stateVersion"], "acceptRisk": True}
    response = await client.post(url, headers=user_headers("durable-" + name), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "PREPARING"
    assert response.json()["phaseReasonCode"] == QUEUED
    replay = await client.post(url, headers=user_headers("durable-" + name), json=payload)
    assert replay.json() == response.json()
    return UUID(task["id"])


@pytest.mark.asyncio
async def test_confirm_survives_api_exit_and_dispatches_once(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_confirmation_enabled = True
        task_id = await enqueue(client, "durable-confirm")
        assert not app.state.confirmation_jobs
        settings = app.state.settings
        adapter, store, kling = (
            app.state.libtv_adapter,
            app.state.object_store,
            app.state.kling_adapter,
        )
        async with app.state.database.session_factory() as session:
            assert not list(await session.scalars(select(GenerationCommand)))
    database = Database(settings.database_url)
    try:

        async def claim():
            async with database.session_factory() as session:
                return await claim_confirmation(session)

        results = await asyncio.gather(claim(), claim())
        winner = [r for r in results if r is not None]
        assert len(winner) == 1
        assert winner[0][1] == task_id
        await execute_confirmation(database, settings, adapter, store, kling, winner[0])
        async with database.session_factory() as session:
            event = await session.get(OutboxEvent, winner[0][0])
            assert event.event_type == FINISHED
            assert len(list(await session.scalars(select(GenerationCommand)))) == 2
        assert await claim() is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_queued_not_stale_and_started_never_replayed(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_confirmation_enabled = True
        task_id = await enqueue(client, "durable-crash")
        database = app.state.database
        async with database.session_factory() as session:
            await session.execute(
                update(ParentTask)
                .where(ParentTask.id == task_id)
                .values(updated_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()
            assert task_id not in await recover_stale_materializations(session, app.state.settings)
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.event_type == REQUESTED)
            )
            assert event
            claim = await claim_confirmation(session)
            assert claim and claim[0] == event.id
        async with database.session_factory() as session:
            assert await claim_confirmation(session) is None
            task = await session.get(ParentTask, task_id)
            assert task.phase_reason_code == RUNNING
        assert await reconcile_abandoned_confirmations(database) == 1
        async with database.session_factory() as session:
            event = await session.get(OutboxEvent, claim[0])
            assert event.event_type == REVIEW
            task = await session.get(ParentTask, task_id)
            assert task.phase == ParentTaskPhase.ACTION_REQUIRED
            assert not list(await session.scalars(select(GenerationCommand)))
            assert await claim_confirmation(session) is None


@pytest.mark.asyncio
async def test_batch_acceptance_is_durable_and_replays(tmp_path):
    async with running_app(tmp_path) as (client, app):
        app.state.settings.classic_durable_confirmation_enabled = True
        first = await create_analyzed_task(client, client_task_id="batch-durable-1")
        second = await create_analyzed_task(client, client_task_id="batch-durable-2")
        payload = {
            "tasks": [
                {
                    "taskId": first["id"],
                    "expectedStateVersion": first["stateVersion"],
                    "acceptRisk": True,
                },
                {
                    "taskId": second["id"],
                    "expectedStateVersion": second["stateVersion"] + 1,
                    "acceptRisk": True,
                },
            ]
        }
        response = await client.post(
            "/api/v1/confirmations/execute", headers=user_headers("batch-durable"), json=payload
        )
        assert response.status_code == 200, response.text
        assert response.json()["succeededCount"] == 1
        assert response.json()["failedCount"] == 1
        replay = await client.post(
            "/api/v1/confirmations/execute", headers=user_headers("batch-durable"), json=payload
        )
        assert replay.json() == response.json()
        async with app.state.database.session_factory() as session:
            claim = await claim_confirmation(session)
            assert claim and claim[1] == UUID(first["id"])
            assert await claim_confirmation(session) is None
