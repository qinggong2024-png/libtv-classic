from __future__ import annotations

import asyncio
import json
import time

import pytest
from libtv_agent.config import Settings
from libtv_agent.domain.models import GenerationCommand, ParentTask
from libtv_agent.runner.classic_checkpoint import CheckpointStop, atomic_json
from libtv_agent.runner.classic_durable_readiness import publish_readiness
from libtv_agent.services.classic_durable_config import (
    DEFAULT,
    NAME,
    READY,
    acceptance_status,
    launcher_environment,
    read_config,
    revision,
    runner_ready,
)
from sqlalchemy import select
from test_workflow_integration import create_draft_task, running_app, user_headers


def configure(settings, path, data):
    atomic_json(path, data)
    settings.classic_durable_config_file = path
    settings.classic_durable_config_revision = revision(data)
    settings.classic_durable_analysis_enabled = data["analysis"]
    settings.classic_durable_confirmation_enabled = data["confirmation"]
    settings.classic_durable_analysis_concurrency = data["analysisConcurrency"]


def ready_receipt(stopping, data, **overrides):
    return {
        **stopping.identity,
        "configRevision": revision(data),
        "analysis": data["analysis"],
        "confirmation": data["confirmation"],
        "analysisConcurrency": data["analysisConcurrency"],
        "heartbeat": time.time(),
        "ready": True,
        **overrides,
    }


def test_default_and_invalid_configuration(tmp_path, monkeypatch):
    path = tmp_path / NAME
    monkeypatch.setenv("CLASSIC_DURABLE_ANALYSIS_ENABLED", "true")
    assert read_config(path) == DEFAULT
    env = launcher_environment(path)
    assert env["CLASSIC_DURABLE_ANALYSIS_ENABLED"] == "false"
    assert env["CLASSIC_DURABLE_CONFIRMATION_ENABLED"] == "false"
    assert env["CLASSIC_DURABLE_CONFIG_REVISION"] == revision(DEFAULT)
    for invalid in (
        {**DEFAULT, "analysis": "true"},
        {**DEFAULT, "analysisConcurrency": True},
        {**DEFAULT, "analysis": True},
        {**DEFAULT, "version": 2},
        {**DEFAULT, "unexpected": 1},
    ):
        atomic_json(path, invalid)
        with pytest.raises(ValueError):
            launcher_environment(path)


def test_ready_requires_matching_live_identity_and_config(tmp_path):
    settings = Settings(_env_file=None)
    path = tmp_path / NAME
    data = {**DEFAULT, "analysis": True, "runnerSourceRevision": "a" * 64}
    configure(settings, path, data)
    assert acceptance_status(settings, "analysis") == "RUNNER_NOT_READY"
    assert acceptance_status(settings, "confirmation") == "DISABLED"
    stopping = CheckpointStop(tmp_path, "runner", "a" * 64)
    target = tmp_path / READY
    atomic_json(target, ready_receipt(stopping, data))
    assert acceptance_status(settings, "analysis") == "READY"
    for bad in (
        {"heartbeat": time.time() - 30},
        {"sourceRevision": "b" * 64},
        {"created": 0},
        {"pid": 2147483647},
        {"token": "bad"},
        {"configRevision": "c" * 64},
        {"analysisConcurrency": 2},
        {"ready": False},
    ):
        atomic_json(target, ready_receipt(stopping, data, **bad))
        assert not runner_ready(tmp_path, data)
    atomic_json(target, ready_receipt(stopping, data))
    atomic_json(stopping.request_path, stopping.identity)
    assert not runner_ready(tmp_path, data)
    atomic_json(path, {**data, "analysisConcurrency": 3})
    assert acceptance_status(settings, "analysis") == "CONFIG_MISMATCH"
    path.write_text("{broken", encoding="utf-8")
    assert acceptance_status(settings, "analysis") == "CONFIG_INVALID"


@pytest.mark.asyncio
async def test_api_rejects_new_requests_but_preserves_accepted_receipt(tmp_path):
    async with running_app(tmp_path) as (client, app):
        data = {**DEFAULT, "analysis": True, "runnerSourceRevision": "a" * 64}
        path = tmp_path / NAME
        configure(app.state.settings, path, data)
        draft = await create_draft_task(client, "guarded-analysis")
        url = f"/api/v1/tasks/{draft['id']}/analyze"
        payload = {"expectedStateVersion": draft["stateVersion"]}
        headers = user_headers("guarded-analysis")
        blocked = await client.post(url, headers=headers, json=payload)
        assert blocked.status_code == 503, blocked.text
        async with app.state.database.session_factory() as session:
            row = await session.scalar(select(ParentTask))
            assert row.phase.value == "DRAFT"
        stopping = CheckpointStop(tmp_path, "runner", "a" * 64)
        atomic_json(tmp_path / READY, ready_receipt(stopping, data))
        accepted = await client.post(url, headers=headers, json=payload)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["analysisStage"] == "DURABLE_QUEUED"
        atomic_json(tmp_path / READY, ready_receipt(stopping, data, ready=False))
        replay = await client.post(url, headers=headers, json=payload)
        assert replay.json() == accepted.json()
        state = (await client.get("/api/v1/system/classic-durable-execution")).json()
        assert state["analysis"] == "RUNNER_NOT_READY"
        assert state["confirmation"] == "DISABLED"
        async with app.state.database.session_factory() as session:
            assert not list(await session.scalars(select(GenerationCommand)))


@pytest.mark.asyncio
async def test_runner_heartbeat_checks_worker_count_and_clears_on_exit(tmp_path):
    async with running_app(tmp_path) as (_, app):
        data = {
            **DEFAULT,
            "analysis": True,
            "analysisConcurrency": 1,
            "runnerSourceRevision": "a" * 64,
        }
        configure(app.state.settings, tmp_path / NAME, data)
        stopping = CheckpointStop(tmp_path, "runner", "a" * 64)
        workers = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        publisher = asyncio.create_task(
            publish_readiness(app.state.database, app.state.settings, stopping, workers)
        )
        try:
            for _ in range(100):
                if runner_ready(tmp_path, data):
                    break
                await asyncio.sleep(0.02)
            assert runner_ready(tmp_path, data)
            workers[0].cancel()
            await asyncio.gather(workers[0], return_exceptions=True)
            for _ in range(150):
                if not runner_ready(tmp_path, data):
                    break
                await asyncio.sleep(0.02)
            assert not runner_ready(tmp_path, data)
        finally:
            publisher.cancel()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(publisher, *workers, return_exceptions=True)
        assert json.loads((tmp_path / READY).read_text())["ready"] is False


def test_rollback_read_only_counts_active_analysis_and_unsettled_outbox(tmp_path):
    import runpy
    import sqlite3
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts/classic_durable_execution.py"
    counts = runpy.run_path(str(script))["rollback_counts"]
    db = tmp_path / "classic.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE parent_task (phase TEXT);
            CREATE TABLE outbox_event (event_type TEXT);
            INSERT INTO parent_task VALUES ('ANALYZING'), ('COMPLETED');
            INSERT INTO outbox_event VALUES ('DURABLE_CONFIRMATION_REQUESTED'),
                ('DURABLE_CONFIRMATION_STARTED'), ('DURABLE_CONFIRMATION_REVIEW'),
                ('DURABLE_CONFIRMATION_FINISHED');
        """)
    before = db.read_bytes()
    assert counts(db) == {"activeAnalysis": 1, "confirmationInFlight": 2, "confirmationReview": 1}
    assert db.read_bytes() == before


def handoff_marker(tmp_path, stopping, data, new_revision):
    from libtv_agent.services.classic_durable_config import HANDOFF

    now = time.time()
    atomic_json(tmp_path / HANDOFF, {
        "version": 1, "configRevision": revision(data),
        "fromRevision": stopping.identity["sourceRevision"], "toRevision": new_revision,
        "checkpoint": stopping.identity, "createdAt": now, "expiresAt": now + 120,
    })
    atomic_json(stopping.request_path, stopping.identity)


def test_online_runner_pin_and_bounded_queue(tmp_path):
    from fastapi import HTTPException
    from libtv_agent.services.classic_durable_config import (
        HANDOFF,
        HANDOFF_QUEUE_STATUS,
        require_acceptance,
    )

    settings = Settings(_env_file=None)
    data = {**DEFAULT, "analysis": True, "confirmation": True, "runnerSourceRevision": "a" * 64}
    configure(settings, tmp_path / NAME, data)
    old = CheckpointStop(tmp_path, "runner", "a" * 64)
    atomic_json(tmp_path / READY, ready_receipt(old, data))
    assert runner_ready(tmp_path, data)
    handoff_marker(tmp_path, old, data, "b" * 64)
    assert acceptance_status(settings, "analysis") == HANDOFF_QUEUE_STATUS
    assert not runner_ready(tmp_path, data)
    require_acceptance(settings, "analysis", allow_handoff_queue=True)
    with pytest.raises(HTTPException):
        require_acceptance(settings, "confirmation")
    old.publish("DRAINED")
    atomic_json(tmp_path / "worker-runner-pin.json", {
        "version": 1, "releaseRoot": str(tmp_path / "new"), "sourceRevision": "b" * 64,
    })
    assert acceptance_status(settings, "analysis") == HANDOFF_QUEUE_STATUS
    new = CheckpointStop(tmp_path, "runner", "b" * 64)
    assert acceptance_status(settings, "analysis") == HANDOFF_QUEUE_STATUS
    atomic_json(tmp_path / READY, ready_receipt(new, data))
    assert runner_ready(tmp_path, data)
    assert acceptance_status(settings, "analysis") == "READY"
    marker = json.loads((tmp_path / HANDOFF).read_text())
    atomic_json(tmp_path / READY, ready_receipt(new, data, ready=False))
    marker["expiresAt"] = time.time() - 1
    atomic_json(tmp_path / HANDOFF, marker)
    assert acceptance_status(settings, "analysis") == "RUNNER_NOT_READY"
    assert read_config(tmp_path / NAME) == data


@pytest.mark.asyncio
async def test_api_durable_acceptance_during_checkpoint(tmp_path):
    from libtv_agent.domain.models import OutboxEvent
    from libtv_agent.services.durable_confirmation import REQUESTED
    from test_workflow_integration import create_analyzed_task

    async with running_app(tmp_path) as (client, app):
        draft = await create_draft_task(client, "handoff-analysis")
        analyzed = await create_analyzed_task(client, client_task_id="handoff-confirm")
        data = {**DEFAULT, "analysis": True, "confirmation": True,
                "runnerSourceRevision": "a" * 64}
        configure(app.state.settings, tmp_path / NAME, data)
        stopping = CheckpointStop(tmp_path, "runner", "a" * 64)
        atomic_json(tmp_path / READY, ready_receipt(stopping, data))
        handoff_marker(tmp_path, stopping, data, "b" * 64)
        for task, action, code in ((draft, "analyze", 202), (analyzed, "confirm", 200)):
            url = f"/api/v1/tasks/{task['id']}/{action}"
            payload = {"expectedStateVersion": task["stateVersion"]}
            if action == "confirm":
                payload["acceptRisk"] = True
            headers = user_headers("handoff-" + action)
            accepted = await client.post(url, headers=headers, json=payload)
            assert accepted.status_code == code, accepted.text
            replay = await client.post(url, headers=headers, json=payload)
            assert replay.json() == accepted.json()
        async with app.state.database.session_factory() as session:
            assert not list(await session.scalars(select(GenerationCommand)))
            assert len(list(await session.scalars(select(OutboxEvent).where(
                OutboxEvent.event_type == REQUESTED
            )))) == 1


@pytest.mark.asyncio
async def test_readiness_transient_atomic_write_failure_recovers(tmp_path, monkeypatch):
    from libtv_agent.runner import classic_durable_readiness as readiness

    async with running_app(tmp_path) as (_, app):
        data = {**DEFAULT, "analysis": True, "analysisConcurrency": 1,
                "runnerSourceRevision": "a" * 64}
        configure(app.state.settings, tmp_path / NAME, data)
        stopping = CheckpointStop(tmp_path, "runner", "a" * 64)
        workers = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        calls = 0

        def flaky(path, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("synthetic concurrent read")
            atomic_json(path, payload)

        monkeypatch.setattr(readiness, "atomic_json", flaky)
        publisher = asyncio.create_task(
            readiness.publish_readiness(app.state.database, app.state.settings, stopping, workers)
        )
        try:
            for _ in range(180):
                if runner_ready(tmp_path, data):
                    break
                await asyncio.sleep(0.02)
            assert calls >= 2 and runner_ready(tmp_path, data)
            assert not publisher.done()
        finally:
            publisher.cancel()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(publisher, *workers, return_exceptions=True)
