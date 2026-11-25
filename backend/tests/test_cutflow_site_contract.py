import json
import uuid

import pytest
import test_cutflow_handoff as shared
from libtv_agent.domain.models import CutflowHandoff, DownloadArtifact, ParentTask
from libtv_agent.runner import cutflow_worker
from libtv_agent.services.cutflow_handoff import (
    CONTRACT,
    CutflowHandoffError,
    handoff_identity,
    publish_package,
    sha256_file,
    site_identity,
)
from sqlalchemy import func, select

handoff_env = shared.handoff_env
enqueue = shared.enqueue

GUIDINGCROSS_SITE_ID = "1ea59ab3-85ab-5134-bfb7-b85be1e13349"


def test_real_catalog_domain_has_stable_uuid5():
    assert site_identity("https://www.guidingcross.com") == {
        "site_id": GUIDINGCROSS_SITE_ID,
        "site_url": "https://www.guidingcross.com/",
    }
    assert site_identity("https://WWW.Guidingcross.com/")["site_id"] == GUIDINGCROSS_SITE_ID
    assert site_identity("https://www.geeksoutfit.com/")["site_id"] != GUIDINGCROSS_SITE_ID


@pytest.mark.parametrize("url", [None, "", "http://10.12.34.56:5173/", "https://10.12.34.56/",
                                 "https://localhost/", "https://user@site.com/", "https://site.com/a",
                                 "https://site.com/?token=x", "https://site.com/#x"])
def test_missing_or_server_site_never_gets_an_identity(url):
    with pytest.raises(CutflowHandoffError):
        site_identity(url)


async def test_v11_manifest_descriptor_and_ready_bind_same_identity(handoff_env):
    database, settings, store, ids = handoff_env
    row = await enqueue(handoff_env)
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        descriptor = await publish_package(row, artifact, settings, store)
    path = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["schema_version"] == row.contract_version == CONTRACT == "libtv-to-cutflow/1.1"
    assert descriptor == {
        "handoffId": row.handoff_key, "userId": str(ids["user"]),
        "siteId": GUIDINGCROSS_SITE_ID, "manifestSha256": sha256_file(path / "manifest.json"),
    }
    assert manifest["source"]["site_id"] == descriptor["siteId"]
    assert manifest["source"]["user_id"] == descriptor["userId"]
    assert manifest["source"]["site_profile_id"] == "guidingcross"
    assert json.loads((path / "READY").read_text())["sha256"] == descriptor["manifestSha256"]


@pytest.mark.parametrize("profile,version", [(None, None), ("missing", "1.0.0"),
                                            ("guidingcross", "obsolete")])
async def test_missing_or_stale_task_site_rejected_without_handoff(handoff_env, profile, version):
    database, settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.site_profile_id, task.site_profile_version = profile, version
        await session.commit()
    with pytest.raises(CutflowHandoffError) as error:
        await enqueue(handoff_env)
    assert error.value.code == "CUTFLOW_SITE_REQUIRED"
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 0
    assert not settings.cutflow_handoff_root.exists()


async def test_worker_poll_uses_both_saved_identity_fields(handoff_env, monkeypatch):
    database, settings, store, ids = handoff_env
    row = await enqueue(handoff_env)
    calls = []

    def remote(_settings, method, suffix, payload=None):
        calls.append((method, suffix, payload))
        return {"handoffId": row.handoff_key, "status": "uploading",
                "batchId": str(uuid.uuid4()), "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    assert calls[0][2]["siteId"] == GUIDINGCROSS_SITE_ID
    assert calls[1][1] == f"/{row.handoff_key}?userId={ids['user']}&siteId={GUIDINGCROSS_SITE_ID}"


@pytest.mark.parametrize("change", ["legacy", "missing-site", "wrong-user", "wrong-domain"])
async def test_invalid_snapshot_stops_before_nas_or_http(handoff_env, monkeypatch, change):
    database, settings, store, _ids = handoff_env
    row = await enqueue(handoff_env)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        source = dict(saved.manifest_json["source"])
        if change == "legacy":
            saved.contract_version = "libtv-to-cutflow/1.0"
        elif change == "missing-site":
            source.pop("site_id")
        elif change == "wrong-user":
            source["user_id"] = str(uuid.uuid4())
        else:
            source["site_url"] = "https://www.geeksoutfit.com/"
        saved.manifest_json = {"source": source}
        await session.commit()

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid identity reached HTTP")

    monkeypatch.setattr(cutflow_worker, "control_request", forbidden)
    await cutflow_worker.tick(database, settings, store)
    assert not settings.cutflow_handoff_root.exists()
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.error_code in {"CUTFLOW_CONTRACT_UNSUPPORTED", "CUTFLOW_IDENTITY_INVALID"}


async def test_saved_descriptor_missing_site_rejected(handoff_env):
    row = await enqueue(handoff_env)
    row.manifest_path = f"inbox/{row.handoff_key}/manifest.json"
    row.manifest_json = {"handoffId": row.handoff_key, "userId": str(row.user_id),
                         "manifestSha256": "a" * 64}
    with pytest.raises(CutflowHandoffError):
        handoff_identity(row)
