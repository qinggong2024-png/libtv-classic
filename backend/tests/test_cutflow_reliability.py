"""Crash/outage/stall checks for the existing job; never calls a paid provider."""

import asyncio
import copy
import uuid
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

import pytest
import test_cutflow_lan as lan
from libtv_agent.config import Settings
from libtv_agent.domain.models import CutflowHandoff
from libtv_agent.runner import cutflow_worker
from libtv_agent.services import cutflow_observation as observation
from sqlalchemy import select, update

base_handoff_env = lan.base_handoff_env
product_handoff_env = lan.product_handoff_env
handoff_env = lan.handoff_env


def sample():
    return {"handoffId": f"handoff-{uuid.uuid4()}", "manifestSha256": "a" * 64,
            "status": "importing", "batchId": None, "outputs": []}


def test_contact_is_not_progress_and_timer_survives_restart(tmp_path):
    settings = Settings(_env_file=None, artifact_root=tmp_path)
    now = datetime.now(UTC)
    response = sample()
    state, warning = observation.observe(settings, {}, response, now)
    assert warning is None
    key = uuid.uuid4()
    observation.save(settings, key, state)
    later, warning = observation.observe(
        settings, observation.load(settings, key), response, now + timedelta(minutes=6),
    )
    assert warning[0] == "CUTFLOW_IMPORT_STALLED"
    assert later["lastProgressAt"] == state["lastProgressAt"]
    response.update(status="editing", batchId=str(uuid.uuid4()))
    progressed, warning = observation.observe(
        settings, later, response, now + timedelta(minutes=7),
    )
    assert warning is None and progressed["lastProgressAt"] != state["lastProgressAt"]


@pytest.mark.parametrize("age,other,alive,expected", [
    (0, False, True, None), (0, True, True, "CUTFLOW_SCOPE_MISMATCH"),
    (0, False, False, "CUTFLOW_WORKER_OFFLINE"), (100, True, True, None),
])
def test_worker_diagnostic_must_match_target_and_be_fresh(age, other, alive, expected):
    settings = Settings(_env_file=None)
    now = datetime.now(UTC)
    response = sample()
    response["diagnostics"] = {
        "schemaVersion": "cutflow-diagnostics/1", "handoffId": response["handoffId"],
        "manifestSha256": response["manifestSha256"],
        "observedAt": (now - timedelta(seconds=age)).isoformat(),
        "workerAlive": alive,
        "workerHandoffIds": ["old-task"] if other else [response["handoffId"]],
    }
    _state, warning = observation.observe(settings, {}, response, now)
    assert (warning[0] if warning else None) == expected
    response["diagnostics"]["handoffId"] = "wrong-task"
    assert observation.observe(settings, {}, response, now)[1] is None


def test_corrupt_observation_is_not_business_failure(tmp_path):
    settings = Settings(_env_file=None, artifact_root=tmp_path)
    key = uuid.uuid4()
    path = observation.path_for(settings, key)
    path.parent.mkdir(parents=True)
    path.write_text("not-json")
    assert observation.load(settings, key) == {}


async def test_import_stall_visible_without_repost_and_recovers(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    posts = []
    stage = "importing"

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            posts.append(copy.deepcopy(payload))
        return {"handoffId": row.handoff_key, "manifestSha256": posts[0]["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(posts[0]),
                "status": stage, "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    cached = observation.load(settings, row.id)
    cached["lastProgressAt"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    observation.save(settings, row.id, cached)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "IMPORTING" and saved.error_code == "CUTFLOW_IMPORT_STALLED"
        assert saved.failure_count == 0 and saved.next_run_at is not None
    stage = "editing"
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None
    assert len(posts) == 1


@pytest.mark.parametrize("failure", ["network", "missing", "server", "auth"])
async def test_accepted_outage_never_resubmits_or_forgets_job(handoff_env, monkeypatch, failure):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    posts = []
    unavailable = False

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            posts.append(copy.deepcopy(payload))
        if unavailable:
            if failure == "network":
                raise URLError("network detail that must not be displayed")
            status = {"missing": 404, "server": 503, "auth": 401}[failure]
            raise cutflow_worker.ControlHTTPError(status)
        return {"handoffId": row.handoff_key, "manifestSha256": posts[0]["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(posts[0]),
                "status": "importing", "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    unavailable = True
    for _ in range(6):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == ("FAILED" if failure == "auth" else "IMPORTING")
        assert saved.error_code == ("CUTFLOW_RECEIPT_MISSING" if failure == "missing"
                                    else "CUTFLOW_RETRY" if failure == "network"
                                    else "CUTFLOW_HTTP_ERROR")
        assert "network detail" not in (saved.error_message or "")
    assert len(posts) == 1
    if failure != "auth":
        unavailable = False
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
        async with database.session_factory() as session:
            assert (await session.get(CutflowHandoff, row.id)).failure_count == 0


async def test_stale_lease_cannot_publish_observation(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await lan.shared.enqueue(handoff_env)
    payload = {}

    def remote(_settings, method, _suffix, data=None):
        if method == "POST":
            payload.update(data)
        return {"handoffId": row.handoff_key, "manifestSha256": payload["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(payload),
                "status": "importing", "outputs": []}

    async def steal_lease(*_args):
        async with database.session_factory() as session:
            await session.execute(update(CutflowHandoff).where(CutflowHandoff.id == row.id)
                                  .values(lease_token="new-worker"))
            await session.commit()
        return []

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    monkeypatch.setattr(cutflow_worker, "import_outputs", steal_lease)
    await cutflow_worker.tick(database, settings, store)
    assert observation.load(settings, row.id) == {}
    async with database.session_factory() as session:
        assert (await session.scalar(select(CutflowHandoff))).lease_token == "new-worker"


async def test_discovery_failure_does_not_starve_polling_or_kill_worker(monkeypatch, caplog):
    settings = Settings(_env_file=None, cutflow_handoff_enabled=True,
                        cutflow_transport="lan", cutflow_handoff_root="unused",
                        cutflow_base_url="http://isolated.test")
    polled = []
    waits = []
    disposed = []

    class FakeDatabase:
        def __init__(self, _url):
            pass

        async def dispose(self):
            disposed.append(True)

    async def broken_discovery(*_args):
        raise RuntimeError("private exception text")

    async def polling(*_args):
        polled.append(True)

    async def sleeping(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(cutflow_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(cutflow_worker, "Database", FakeDatabase)
    monkeypatch.setattr(cutflow_worker, "create_object_store", lambda _: object())
    monkeypatch.setattr(cutflow_worker, "enqueue_completed", broken_discovery)
    monkeypatch.setattr(cutflow_worker, "tick", polling)
    monkeypatch.setattr(cutflow_worker.asyncio, "sleep", sleeping)
    with pytest.raises(asyncio.CancelledError):
        await cutflow_worker.run(once=False)
    # The 30s admission sweep is throttled after the first discovery failure;
    # status polling still runs on the next healthy 1s cycle.
    assert len(polled) == 2 and waits == [15, 1] and disposed == [True]
    assert "CUTFLOW_WORKER_CYCLE_FAILED" in caplog.text
    assert "operation=enqueue_completed" in caplog.text
    assert "error_code=RuntimeError" in caplog.text
    assert "private exception text" not in caplog.text
