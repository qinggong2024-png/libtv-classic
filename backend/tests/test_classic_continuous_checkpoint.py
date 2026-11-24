"""No-provider fault injection for the Classic continuous-release candidate."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from libtv_agent.app import _owned_recovery
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import Base, SubjectElementJob
from libtv_agent.runner.classic_checkpoint import CheckpointStop, atomic_json
from libtv_agent.services.classic_process_lock import try_lock
from libtv_agent.services.subject_elements import _run_subject_element_attempt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from classic_worker_source import worker_source  # noqa: E402


def settings(port, route):
    return Settings(_env_file=None, app_env="test", app_port=port, classic_api_route_file=route)


def test_recovery_lock_excludes_another_process(tmp_path):
    route = tmp_path / "api-routing.json"
    code = """
from pathlib import Path
from types import SimpleNamespace
import sys
from libtv_agent.services.classic_process_lock import try_lock
with try_lock(SimpleNamespace(classic_api_route_file=Path(sys.argv[1])), "recovery") as acquired:
    print(int(acquired))
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend" / "src")}
    with try_lock(settings(8002, route), "recovery") as acquired:
        assert acquired
        result = subprocess.run(
            [sys.executable, "-c", code, str(route)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        assert result.stdout.strip() == "0"
    result = subprocess.run(
        [sys.executable, "-c", code, str(route)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "1"


@pytest.mark.asyncio
async def test_route_flip_defers_new_recovery_while_old_sweep_in_progress(tmp_path):
    route = tmp_path / "route.json"
    atomic_json(route, {"version": 1, "activeApiPort": 8002})
    blue, green = [
        SimpleNamespace(
            state=SimpleNamespace(settings=settings(port, route), classic_recovery_in_flight=0)
        )
        for port in (8002, 8003)
    ]
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def work(app):
        calls.append(app.state.settings.app_port)
        if app is blue:
            entered.set()
            await release.wait()
        return 1

    running = asyncio.create_task(_owned_recovery(blue, work))
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        atomic_json(route, {"version": 1, "activeApiPort": 8003})
        assert await _owned_recovery(green, work) is None
        assert blue.state.classic_recovery_in_flight == 1
    finally:
        release.set()
        await running
    assert await _owned_recovery(green, work) == 1
    assert calls == [8002, 8003]
    assert blue.state.classic_recovery_in_flight == 0


@pytest.mark.asyncio
async def test_interrupted_subject_never_calls_provider_again(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'subject.db').as_posix()}")
    task_id, user_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            job = SubjectElementJob(
                parent_task_id=task_id,
                user_id=user_id,
                external_task_id="original-external-id",
                name="subject",
                description="test",
                status="PROCESSING",
                attempt_count=1,
            )
            session.add(job)
            await session.commit()
            job_id = job.id
        app = SimpleNamespace(
            state=SimpleNamespace(
                database=database,
                settings=settings(8003, tmp_path / "route.json"),
            )
        )
        # No adapter exists: this test would fail if it tries a provider call.
        assert await _run_subject_element_attempt(app, task_id=task_id, user_id=user_id) == (
            job_id,
            False,
        )
        async with database.session_factory() as session:
            job = await session.get(SubjectElementJob, job_id)
            assert job.status == "FAILED"
            assert job.last_error_code == "SUBJECT_ELEMENT_RESULT_UNKNOWN"
            assert job.external_task_id == "original-external-id"
            assert job.next_attempt_at is None
            assert job.attempt_count == 1
    finally:
        await database.dispose()


def test_checkpoint_request_cannot_stop_next_process(tmp_path):
    old = CheckpointStop(tmp_path, "runner", "a" * 64)
    atomic_json(old.request_path, old.identity)
    assert old.wait(0.1)
    old.publish("DRAINED")
    assert json.loads(old.status_path.read_text())["state"] == "DRAINED"
    new = CheckpointStop(tmp_path, "runner", "b" * 64)
    assert not new.is_set()
    assert old.identity["token"] != new.identity["token"]
    assert not CheckpointStop(tmp_path, "cutflow", "a" * 64).is_set()


def test_worker_pin_refuses_mutable_or_mismatched_release(tmp_path, monkeypatch):
    import classic_active_release_status

    root, release = tmp_path / "main", tmp_path / "release"
    root.mkdir()
    release.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(
        classic_active_release_status,
        "workspace_source_revision",
        lambda p: "a" * 64 if p == root else "b" * 64,
    )
    assert worker_source("runner", runtime, root) == (root, "a" * 64)
    path = runtime / "worker-runner-pin.json"
    atomic_json(path, {"version": 1, "releaseRoot": str(release), "sourceRevision": "b" * 64})
    assert worker_source("runner", runtime, root) == (release, "b" * 64)
    assert worker_source("cutflow", runtime, root) == (root, "a" * 64)
    atomic_json(path, {"version": 1, "releaseRoot": str(release), "sourceRevision": "c" * 64})
    with pytest.raises(RuntimeError, match="fingerprint"):
        worker_source("runner", runtime, root)


@pytest.mark.asyncio
async def test_cutflow_drains_current_operation_without_starting_next(tmp_path, monkeypatch):
    from libtv_agent.runner import cutflow_worker as cut

    stopping = CheckpointStop(tmp_path, "cutflow", "a" * 64)
    config = SimpleNamespace(cutflow_multi_source_enabled=False, cutflow_transport="bridge")
    calls = []

    class DB:
        async def dispose(self):
            calls.append("dispose")

    async def operation(*args):
        calls.append("enqueue-start")
        atomic_json(stopping.request_path, stopping.identity)
        await asyncio.sleep(0)
        calls.append("enqueue-finish")

    async def forbidden(*args):
        pytest.fail("worker began another operation after checkpoint")

    async def pulse(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(cut, "get_settings", lambda: config)
    monkeypatch.setattr(cut, "require_configuration", lambda *_: None)
    config.database_url = "fake"
    monkeypatch.setattr(cut, "Database", lambda *_: DB())
    monkeypatch.setattr(cut, "create_object_store", lambda *_: object())
    monkeypatch.setattr(cut, "_worker_pulse", pulse)
    monkeypatch.setattr(cut, "enqueue_completed", operation)
    monkeypatch.setattr(cut, "tick", forbidden)
    monkeypatch.setattr(cut, "tick_multi", forbidden)
    await cut.run(once=False, stopping=stopping)
    assert calls == ["enqueue-start", "enqueue-finish", "dispose"]
    assert json.loads(stopping.status_path.read_text())["state"] == "DRAINED"


def test_controller_refuses_pin_until_exact_checkpoint_and_exit(tmp_path, monkeypatch):
    import classic_worker_checkpoint as ctl

    root = tmp_path / "main"
    release = tmp_path / "candidate"
    root.mkdir()
    release.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    stack = runtime / "stack.json"
    atomic_json(
        stack, {"apiPort": 8002, "webPort": 5176, "processes": [{"name": "runner", "pid": 100}]}
    )
    monkeypatch.setattr(ctl.classic_lan, "STATE", stack)
    monkeypatch.setattr(ctl, "supervisor_supports_blue", lambda *_: True)
    monkeypatch.setattr(ctl, "preflight", lambda **_: {})
    monkeypatch.setattr(ctl, "validated_release", lambda path, revision: path)
    monkeypatch.setattr(ctl, "verify_worker_compatibility", lambda *_: None)
    monkeypatch.setattr(ctl, "process_matches", lambda *_: True)
    monkeypatch.setattr(ctl, "restart_held", lambda *_: True)
    monkeypatch.setattr(ctl, "arm_handoff", lambda **_: {})
    monkeypatch.setattr(ctl.classic_lan, "owned_process", lambda *_: object())
    stopping = CheckpointStop(runtime, "runner", "a" * 64)
    path = runtime / "worker-runner-pin.json"
    dry = ctl.request_checkpoint("runner", "a" * 64, baseline_unknown=15)
    assert dry["action"] == "dry-run" and not stopping.request_path.exists()
    ctl.request_checkpoint("runner", "a" * 64, baseline_unknown=15, apply=True)
    assert stopping.is_set()
    with pytest.raises(RuntimeError, match="receipt"):
        ctl.install_pin(
            "runner", release, "b" * 64, expected_old="a" * 64, baseline_unknown=15, apply=True
        )
    stopping.publish("DRAINED")
    with pytest.raises(RuntimeError, match="not exited"):
        ctl.install_pin(
            "runner", release, "b" * 64, expected_old="a" * 64, baseline_unknown=15, apply=True
        )
    assert not path.exists()
    monkeypatch.setattr(ctl.classic_lan, "owned_process", lambda *_: None)
    result = ctl.install_pin(
        "runner", release, "b" * 64, expected_old="a" * 64, baseline_unknown=15, apply=True
    )
    assert result["action"] == "pinned-restart-held"
    assert json.loads(path.read_text())["sourceRevision"] == "b" * 64
    assert not (runtime / "worker-cutflow-pin.json").exists()


@pytest.mark.asyncio
async def test_subject_transport_failure_after_dispatch_requires_review(tmp_path):
    from libtv_agent.services.subject_elements import _record_subject_element_failure

    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'subject.db').as_posix()}")
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            job = SubjectElementJob(
                parent_task_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                external_task_id="original-external-id",
                name="subject",
                description="test",
                status="PROCESSING",
                attempt_count=1,
            )
            session.add(job)
            await session.commit()
            job_id = job.id
        app = SimpleNamespace(
            state=SimpleNamespace(
                database=database,
                settings=settings(8003, tmp_path / "route.json"),
            )
        )
        assert not await _record_subject_element_failure(
            app, job_id=job_id, cause=TimeoutError("synthetic"), dispatch_started=True
        )
        async with database.session_factory() as session:
            job = await session.get(SubjectElementJob, job_id)
            assert job.last_error_code == "SUBJECT_ELEMENT_RESULT_UNKNOWN"
            assert job.next_attempt_at is None
            assert job.external_task_id == "original-external-id"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_two_api_instances_accept_durable_jobs_across_two_route_flips(tmp_path):
    from httpx import ASGITransport, AsyncClient
    from libtv_agent.app import create_app
    from libtv_agent.domain.models import GenerationCommand
    from libtv_agent.services.durable_analysis import claim_next
    from sqlalchemy import select
    from test_durable_analysis import enqueue
    from test_workflow_integration import running_app, user_headers

    route = tmp_path / "route.json"
    atomic_json(route, {"version": 1, "activeApiPort": 8002})
    async with running_app(tmp_path / "shared") as (blue_client, blue):
        blue.state.settings.app_port = 8002
        blue.state.settings.classic_api_route_file = route
        blue.state.settings.classic_durable_analysis_enabled = True
        green_settings = blue.state.settings.model_copy(update={"app_port": 8003})
        green = create_app(green_settings)
        async with green.router.lifespan_context(green):
            async with AsyncClient(
                transport=ASGITransport(app=green), base_url="http://test"
            ) as green_client:
                first, receipt, url, payload = await enqueue(blue_client, "before-switch")
                atomic_json(route, {"version": 1, "activeApiPort": 8003})
                replay = await green_client.post(
                    url, headers=user_headers("analyze-before-switch"), json=payload
                )
                assert replay.status_code == 202
                assert replay.json() == receipt
                second, *_ = await enqueue(green_client, "after-switch")
                assert (await blue_client.get("/api/v1/system/classic-api-drain")).json()[
                    "retirementReady"
                ]
                atomic_json(route, {"version": 1, "activeApiPort": 8002})
                third, *_ = await enqueue(blue_client, "after-switch-back")
                assert (await green_client.get("/api/v1/system/classic-api-drain")).json()[
                    "retirementReady"
                ]
                async with blue.state.database.session_factory() as session:
                    claimed = [await claim_next(session, stale_seconds=660) for _ in range(3)]
                    assert {claim[0] for claim in claimed} == {first, second, third}
                    assert await claim_next(session, stale_seconds=660) is None
                    assert not list(await session.scalars(select(GenerationCommand)))


def test_worker_only_pin_rejects_schema_or_dependency_changes(tmp_path):
    from classic_worker_checkpoint import verify_worker_compatibility

    root, release = tmp_path / "main", tmp_path / "candidate"
    for base in (root, release):
        for relative in (
            "pyproject.toml",
            "uv.lock",
            "backend/alembic.ini",
            "backend/src/libtv_agent/domain/models.py",
            "backend/src/libtv_agent/domain/enums.py",
            "backend/alembic/versions/0001.py",
        ):
            file = base / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("same source", encoding="utf-8")
    verify_worker_compatibility(root, release)
    (release / "backend/alembic/versions/0001.py").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="migrations"):
        verify_worker_compatibility(root, release)
    (release / "uv.lock").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uv.lock"):
        verify_worker_compatibility(root, release)
