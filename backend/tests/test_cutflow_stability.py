"""Recovery and owner-visible telemetry contracts, using isolated NAS and HTTP fixtures."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import test_cutflow_lan as lan
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.models import CutflowHandoff
from libtv_agent.runner import cutflow_worker
from libtv_agent.services import cutflow_observation as observation
from sqlalchemy import update

base_handoff_env = lan.base_handoff_env
product_handoff_env = lan.product_handoff_env
handoff_env = lan.handoff_env


def client_for(env):
    database, settings, store, _ids = env
    app = create_app(settings)
    app.state.database, app.state.settings, app.state.object_store = database, settings, store
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_accepted_receipt_without_batch_recovers_without_post(handoff_env, monkeypatch):
    database, settings, store, ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    descriptor = {}
    calls = []
    failed = False

    def remote(_settings, method, suffix, payload=None):
        calls.append(method)
        if method == "POST":
            descriptor.update(payload)
        if failed:
            raise cutflow_worker.ControlHTTPError(401)
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor),
                "status": "importing", "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    failed = True
    for _ in range(5):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "FAILED" and saved.cutflow_batch_id is None
    async with client_for(handoff_env) as client:
        result = await client.post(f"/api/v1/cutflow-handoffs/{row.id}/retry", headers={
            "X-User-Id": str(ids["user"]),
        })
        assert result.status_code == 200 and result.json()["status"] == "IMPORTING"
    failed = False
    await cutflow_worker.tick(database, settings, store)
    assert calls.count("POST") == 1
    assert observation.load(settings, row.id)["handoffId"] == row.handoff_key


async def test_nas_outage_preserves_receipt_and_recovers(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    descriptor = {}
    posts = []
    unavailable = True
    batch_id = str(uuid.uuid4())

    def remote(_settings, method, suffix, payload=None):
        if method == "POST":
            posts.append(payload)
            descriptor.update(payload)
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor),
                "status": "completed", "batchId": batch_id, "outputs": [{"id": "published"}]}

    async def importing(*_args):
        if unavailable:
            raise PermissionError("private NAS path and credentials must never escape")
        return [{"id": "published", "review_status": "PENDING"}]

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    monkeypatch.setattr(cutflow_worker, "import_outputs", importing)
    for _ in range(7):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.failure_count == 7
        assert saved.cutflow_batch_id == batch_id
        assert saved.error_code == "CUTFLOW_MEDIA_UNAVAILABLE"
        assert "private NAS" not in saved.error_message
        assert saved.next_run_at is not None
    unavailable = False
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW" and saved.error_code is None
    assert len(posts) == 1


async def test_diagnostics_is_local_owner_scoped_and_expires(handoff_env, monkeypatch):
    database, settings, store, ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    descriptor = {}
    now = datetime.now(UTC)

    def remote(_settings, method, suffix, payload=None):
        if payload:
            descriptor.update(payload)
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor),
                "status": "editing", "outputs": [], "diagnostics": {
                    "schemaVersion": "cutflow-diagnostics/1", "handoffId": row.handoff_key,
                    "manifestSha256": descriptor["manifestSha256"], "observedAt": now.isoformat(),
                    "workerAlive": True, "workerHandoffIds": [row.handoff_key], "stage": "render",
                    "secret": "NEVER_EXPOSE", "rawLog": "NEVER_EXPOSE",
                }}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    observation.worker_pulse(settings, now)

    def forbidden(*_args):
        pytest.fail("read diagnostics contacted CUT")

    monkeypatch.setattr(cutflow_worker, "control_request", forbidden)
    endpoint = f"/api/v1/cutflow-handoffs/{row.id}/diagnostics"
    async with client_for(handoff_env) as client:
        assert (await client.get(endpoint, headers={
            "X-User-Id": str(ids["other"]),
        })).status_code == 404
        result = await client.get(endpoint, headers={"X-User-Id": str(ids["user"])})
        assert result.status_code == 200
        data = result.json()
        assert data["diagnosticsState"] == "available" and data["workerAlive"] is True
        assert data["scopeMatches"] is True and data["sender"]["state"] == "online"
        assert data["remoteStage"] == "render" and "NEVER_EXPOSE" not in result.text
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        snapshot = observation.public_snapshot(settings, saved, now + timedelta(minutes=3))
    assert snapshot["diagnosticsState"] == "stale" and snapshot["workerAlive"] is None
    assert snapshot["scopeMatches"] is None and snapshot["sender"]["state"] == "stale"
    assert snapshot["lastProgressAt"]


async def test_retry_has_atomic_conflict_and_rejects_live_lease(handoff_env):
    database, _settings, _store, ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    endpoint = f"/api/v1/cutflow-handoffs/{row.id}/retry"
    headers = {"X-User-Id": str(ids["user"])}
    async with database.session_factory() as session:
        await session.execute(update(CutflowHandoff).where(CutflowHandoff.id == row.id).values(
            status="FAILED", lease_token="live",
            lease_until=datetime.now(UTC) + timedelta(minutes=2),
        ))
        await session.commit()
    async with client_for(handoff_env) as client:
        assert (await client.post(endpoint, headers=headers)).status_code == 409
        async with database.session_factory() as session:
            await session.execute(update(CutflowHandoff).where(CutflowHandoff.id == row.id).values(
                lease_until=datetime.now(UTC) - timedelta(seconds=1),
            ))
            await session.commit()
        results = await asyncio.gather(*(client.post(endpoint, headers=headers) for _ in range(2)))
        assert sorted(r.status_code for r in results) == [200, 409]


async def test_lost_fence_after_package_publish_never_posts(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    original = cutflow_worker.publish_package

    async def publish(*args, **kwargs):
        result = await original(*args, **kwargs)
        async with database.session_factory() as session:
            await session.execute(update(CutflowHandoff).where(CutflowHandoff.id == row.id).values(
                lease_token="replacement-worker",
            ))
            await session.commit()
        return result

    def forbidden(*_args):
        pytest.fail("stale worker sent notification")

    monkeypatch.setattr(cutflow_worker, "publish_package", publish)
    monkeypatch.setattr(cutflow_worker, "control_request", forbidden)
    assert await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.lease_token == "replacement-worker"
        assert not saved.manifest_path


async def test_restart_after_post_before_first_get_uses_persisted_acceptance(
    handoff_env, monkeypatch,
):
    database, settings, store, ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    descriptor, calls = {}, []
    interrupted, unauthorized = True, False

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        if method == "POST":
            descriptor.update(payload)
        elif unauthorized:
            raise cutflow_worker.ControlHTTPError(401)
        elif interrupted:
            raise asyncio.CancelledError
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor),
                "status": "importing", "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    with pytest.raises(asyncio.CancelledError):
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "IMPORTING" and saved.lease_token
        saved.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    interrupted = False
    await cutflow_worker.tick(database, settings, store)
    assert calls == ["POST", "GET", "GET"]
    # Even lost telemetry plus later permanent auth failure must not erase the
    # durable acceptance or allow manual recovery to POST another edit.
    unauthorized = True
    for _ in range(5):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    observation.path_for(settings, row.id).unlink(missing_ok=True)
    async with client_for(handoff_env) as client:
        result = await client.post(f"/api/v1/cutflow-handoffs/{row.id}/retry", headers={
            "X-User-Id": str(ids["user"]),
        })
        assert result.status_code == 200 and result.json()["status"] == "IMPORTING"
    unauthorized = False
    await cutflow_worker.tick(database, settings, store)
    assert calls.count("POST") == 1


async def test_nas_recovery_rejects_changed_remote_batch(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    descriptor = {}
    batch_id = str(uuid.uuid4())
    imports = []

    def remote(_settings, method, _suffix, payload=None):
        if payload:
            descriptor.update(payload)
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor),
                "status": "completed", "batchId": batch_id, "outputs": [{"id": "published"}]}

    async def unavailable(*_args):
        imports.append(True)
        raise PermissionError

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    monkeypatch.setattr(cutflow_worker, "import_outputs", unavailable)
    await cutflow_worker.tick(database, settings, store)
    original = batch_id
    batch_id = str(uuid.uuid4())
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.cutflow_batch_id == original and saved.error_code == "INVALID_RESULT"
    assert len(imports) == 1
