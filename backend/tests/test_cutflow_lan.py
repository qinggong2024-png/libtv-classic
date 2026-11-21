"""NAS file + HTTP status integration, no CUT service or paid providers."""

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import test_cutflow_auto_edit as automatic
import test_cutflow_handoff as shared
from libtv_agent.api.cutflow_handoffs import _read
from libtv_agent.domain.models import CutflowHandoff, ParentTask
from libtv_agent.runner import cutflow_worker
from libtv_agent.services.cutflow_handoff import CutflowHandoffError, sha256_file
from libtv_agent.services.cutflow_policy import (
    LAN_CONTRACT,
    lan_policy,
    legacy_lan_policy,
    saved_policy,
)
from libtv_agent.templates.site_style_profiles import get_site_style_profile
from sqlalchemy import func, select

base_handoff_env = shared.handoff_env
product_handoff_env = automatic.handoff_env


@pytest.fixture
async def handoff_env(product_handoff_env):
    database, settings, store, ids = product_handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_service_token = None
    settings.cutflow_ootd_template_id = None
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.site_profile_id = None
        task.site_profile_version = None
        await session.commit()
    return product_handoff_env


async def due(database, row):
    async with database.session_factory() as session:
        record = await session.get(CutflowHandoff, row.id)
        record.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


def policy_receipt(descriptor):
    return {
        "state": "accepted", "policyVersion": "yiying-ootd/2", "strategy": "guided_auto",
        "manifestSha256": descriptor["manifestSha256"],
    }


async def test_lan_requires_no_site_template_or_token_and_keeps_ownership(handoff_env):
    database, settings, _store, ids = handoff_env
    row = await shared.enqueue(handoff_env)
    same = await shared.enqueue(handoff_env)
    assert row.id == same.id
    assert row.contract_version == LAN_CONTRACT
    assert saved_policy(row) == lan_policy()
    assert "templateId" not in saved_policy(row)
    assert _read(row)["editMode"] == "automatic"
    assert settings.cutflow_service_token is None
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1
    with pytest.raises(CutflowHandoffError, match="不存在"):
        await shared.enqueue(handoff_env, user=ids["other"])


@pytest.mark.parametrize("token", [None, "local-test-channel-token"])
@pytest.mark.parametrize("published_status", ["review", "completed"])
async def test_real_http_notification_progress_and_verified_nas_return(
    handoff_env, tmp_path, token, published_status,
):
    """A real local HTTP server exchanges status; all media travels via disk."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("media validation requires ffmpeg and ffprobe")
    database, settings, store, ids = handoff_env
    settings.cutflow_service_token = token
    row = await shared.enqueue(handoff_env)
    state = {"status": "editing", "posts": 0, "gets": 0, "descriptor": None}
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self):
            try:
                assert self.headers.get("Authorization") == (f"Bearer {token}" if token else None)
                descriptor = state["descriptor"]
                body = {
                    "handoffId": row.handoff_key, "status": state["status"],
                    "manifestSha256": descriptor["manifestSha256"],
                    "editPolicyReceipt": policy_receipt(descriptor),
                    "outputs": state.get("outputs", []),
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            except Exception as error:
                failures.append(repr(error))
                self.send_error(500)

        def do_POST(self):
            state["posts"] += 1
            try:
                assert self.path == "/api/integrations/libtv"
                descriptor = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert descriptor["contractVersion"] == LAN_CONTRACT
                package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
                assert descriptor["manifestRelativePath"] == (
                    f"inbox/{row.handoff_key}/manifest.json"
                )
                digest = sha256_file(package / "manifest.json")
                assert json.loads((package / "READY").read_text())["sha256"] == digest
                assert descriptor["manifestSha256"] == digest
                manifest = json.loads((package / "manifest.json").read_text())
                assert manifest["edit_policy"] == lan_policy()
                assert manifest["edit_policy"]["strategy"] == "guided_auto"
                assert manifest["edit_policy"]["selectionPolicy"]["removeVisualFailures"] is True
                assert (
                    manifest["edit_policy"]["selectionPolicy"]["removeSlowOrRedundantShots"] is True
                )
                assert manifest["edit_policy"]["selectionPolicy"]["allowCrossAssetAssembly"] is True
                assert manifest["edit_policy"]["audioPolicy"] == {
                    "sourceAudio": "mute",
                    "bgm": "auto_match",
                    "beatSync": True,
                }
                assert manifest["edit_intent"] == {
                    "mode": "guided_auto",
                    "video_type": "fashion_product_video",
                    "remove_visual_failures": True,
                    "remove_slow_or_redundant_shots": True,
                    "remove_unclear_product_shots": True,
                    "allow_multi_segment_assembly": True,
                    "preserve_source_order": True,
                    "preserve_source_audio": False,
                    "bgm": "auto_match",
                    "beat_sync": True,
                }
                assert manifest["source"]["site_profile_id"] is None
                for item in [*manifest["assets"], *manifest["product_references"]]:
                    assert sha256_file(package / item["relative_path"]) == item["sha256"]
                assert manifest["product_references"]
                if state["descriptor"] is not None:
                    assert descriptor == state["descriptor"]
                state["descriptor"] = descriptor
                self.reply()
            except Exception as error:
                failures.append(repr(error))
                self.send_error(500)

        def do_GET(self):
            state["gets"] += 1
            assert self.path == f"/api/integrations/libtv/{row.handoff_key}"
            self.reply()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.cutflow_base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        await cutflow_worker.tick(database, settings, store)
        async with database.session_factory() as session:
            saved = await session.get(CutflowHandoff, row.id)
            assert saved.status == "EDITING" and saved.error_code is None
            assert saved.cutflow_editor_url is None
        # An accepted LAN job polls status without another submission, even without batchId.
        await due(database, row)
        await cutflow_worker.tick(database, settings, store)
        assert state["posts"] == 1 and not failures
        output_root = settings.cutflow_handoff_root / "outbox" / row.handoff_key
        output_root.mkdir(parents=True)
        video = output_root / "render-1.mp4"
        await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=160x288:rate=10", "-t", "1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(video),
        ], check=True, capture_output=True)
        state.update(status=published_status, outputs=[{
            "id": "render-1", "relative_path": "render-1.mp4",
            "sha256": sha256_file(video), "size_bytes": video.stat().st_size,
        }])
        await due(database, row)
        await cutflow_worker.tick(database, settings, store)
        async with database.session_factory() as session:
            saved = await session.get(CutflowHandoff, row.id)
            assert saved.status == "PENDING_REVIEW" and saved.error_code is None
            assert len(saved.outputs_json) == 1
            result = saved.outputs_json[0]
            assert result["sha256"] == sha256_file(video)
            assert result["review_status"] == "PENDING"
            local = await store.localize(result["object_key"], tmp_path / "readback.mp4")
            assert local.read_bytes() == video.read_bytes()
        await due(database, row)
        assert not await cutflow_worker.tick(database, settings, store)
        async with database.session_factory() as session:
            assert len((await session.get(CutflowHandoff, row.id)).outputs_json) == 1
        assert failures == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("case", ["wrong-hash", "empty-complete", "partial-output", "failed"])
async def test_lan_status_cannot_publish_wrong_or_incomplete_output(handoff_env, monkeypatch, case):
    database, settings, store, _ids = handoff_env
    row = await shared.enqueue(handoff_env)
    descriptor = {}

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            descriptor.update(payload)
        return {
            "handoffId": row.handoff_key,
            "editPolicyReceipt": policy_receipt(descriptor),
            "manifestSha256": "a" * 64 if case == "wrong-hash"
            else descriptor["manifestSha256"],
            "status": "failed" if case == "failed" else "completed"
            if case == "empty-complete" else "editing",
            "outputs": [{"id": "unexpected"}] if case == "partial-output" else [],
            "error": {"code": "QUALITY_REJECTED"},
        }

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid result reached media import")

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    monkeypatch.setattr(cutflow_worker, "import_outputs", forbidden)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.outputs_json == []
        assert saved.error_code == ("CUTFLOW_QUALITY_REJECTED" if case == "failed"
                                    else "INVALID_RESULT")
        if case == "failed":
            assert saved.status == "FAILED" and saved.next_run_at is None


async def test_switching_transport_does_not_consume_legacy_jobs(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "bridge"
    settings.cutflow_service_token = "isolated-legacy-channel"
    async with database.session_factory() as session:
        task = await session.get(ParentTask, handoff_env[3]["task"])
        task.site_profile_id = "guidingcross"
        task.site_profile_version = get_site_style_profile("guidingcross").version
        await session.commit()
    await shared.enqueue(handoff_env)
    settings.cutflow_transport = "lan"

    def forbidden(*args, **kwargs):
        pytest.fail("LAN worker consumed legacy job")

    monkeypatch.setattr(cutflow_worker, "control_request", forbidden)
    assert not await cutflow_worker.tick(database, settings, store)


async def test_retry_returns_committed_state_and_only_polls_existing_batch(
    handoff_env, monkeypatch,
):
    from httpx import ASGITransport, AsyncClient
    from libtv_agent.app import create_app
    from libtv_agent.domain.enums import UserRole
    from libtv_agent.domain.models import User

    database, settings, store, ids = handoff_env
    row = await shared.enqueue(handoff_env)
    batch = str(uuid.uuid4())
    descriptor = {"manifestSha256": "a" * 64, "editPolicy": lan_policy(),
                  "userId": str(ids["user"]), "handoffId": row.handoff_key}
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        saved.status = "FAILED"
        saved.manifest_path = f"inbox/{row.handoff_key}/manifest.json"
        saved.manifest_json = descriptor
        saved.cutflow_batch_id = batch
        saved.error_code = "CUTFLOW_EDIT_FAILED"
        saved.failure_count = 1
        await session.commit()
    app = create_app(settings)
    app.state.database, app.state.settings, app.state.object_store = database, settings, store
    endpoint = f"/api/v1/cutflow-handoffs/{row.id}/retry"
    headers = {"X-User-Id": str(ids["user"])}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(endpoint, headers={
            "X-User-Id": str(ids["other"]),
        })).status_code == 404
        async with database.session_factory() as session:
            owner = await session.get(User, ids["user"])
            owner.role = UserRole.AUDITOR
            await session.commit()
        assert (await client.post(endpoint, headers=headers)).status_code == 403
        async with database.session_factory() as session:
            owner = await session.get(User, ids["user"])
            owner.role = UserRole.USER
            await session.commit()
        response = await client.post(endpoint, headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "IMPORTING" and result["updatedAt"]
        assert result["cutflowBatchId"] == batch and result["errorCode"] is None
        assert (await client.post(endpoint, headers=headers)).status_code == 409

    def remote(_settings, method, suffix, payload=None):
        assert method == "GET" and suffix == f"/{row.handoff_key}"
        return {"handoffId": row.handoff_key, **descriptor, "batchId": batch,
                "editPolicyReceipt": policy_receipt(descriptor),
                "status": "editing", "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    assert await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.failure_count == 0
        assert saved.manifest_json == {
            **descriptor, "_acceptedManifestSha256": descriptor["manifestSha256"],
        }
        assert saved.cutflow_batch_id == batch
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1


async def test_lost_lan_submit_response_replays_same_persisted_request(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await shared.enqueue(handoff_env)
    posts = []

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            posts.append(dict(payload))
            if len(posts) == 1:
                raise TimeoutError("accepted but response lost")
        return {
            "handoffId": row.handoff_key, "manifestSha256": posts[-1]["manifestSha256"],
            "editPolicyReceipt": policy_receipt(posts[-1]),
            "status": "editing", "outputs": [],
        }

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    published_hash = sha256_file(package / "manifest.json")
    await due(database, row)
    await cutflow_worker.tick(database, settings, store)
    assert len(posts) == 2 and posts[0] == posts[1]
    assert sha256_file(package / "manifest.json") == published_hash
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1


@pytest.mark.parametrize("method", ["POST", "GET"])
@pytest.mark.parametrize("defect", ["missing", "rejected", "version", "strategy", "hash"])
async def test_guided_policy_requires_matching_receipt_on_post_and_every_get(
    handoff_env, monkeypatch, method, defect,
):
    database, settings, store, _ids = handoff_env
    row = await shared.enqueue(handoff_env)
    descriptor, calls, imports = {}, [], []
    broken = True

    def remote(_settings, verb, _suffix, payload=None):
        calls.append(verb)
        if verb == "POST":
            descriptor.update(payload)
        receipt = policy_receipt(descriptor)
        if broken and verb == method:
            if defect == "missing":
                receipt = None
            else:
                field, value = {
                    "rejected": ("state", "rejected"),
                    "version": ("policyVersion", "yiying-ootd/1"),
                    "strategy": ("strategy", "fixed_ootd"),
                    "hash": ("manifestSha256", "0" * 64),
                }[defect]
                receipt[field] = value
        return {
            "handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
            "status": "completed", "outputs": [{"id": "candidate"}],
            "editPolicyReceipt": receipt,
        }

    async def importing(*_args):
        imports.append(True)
        return [{"id": "candidate", "review_status": "PENDING"}]

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    monkeypatch.setattr(cutflow_worker, "import_outputs", importing)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.error_code == "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED"
        assert saved.outputs_json == [] and not imports
        assert saved.manifest_json["_acceptedManifestSha256"] == descriptor["manifestSha256"]
    assert calls == (["POST"] if method == "POST" else ["POST", "GET"])
    # A later valid GET can recover, without submitting another edit or replacing the manifest.
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    original = (package / "manifest.json").read_bytes()
    broken = False
    await due(database, row)
    await cutflow_worker.tick(database, settings, store)
    assert calls.count("POST") == 1 and len(imports) == 1
    assert (package / "manifest.json").read_bytes() == original
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW" and saved.error_code is None


async def test_guided_get_cannot_drop_receipt_after_prior_acceptance(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await shared.enqueue(handoff_env)
    descriptor, calls = {}, []
    missing = False

    def remote(_settings, verb, _suffix, payload=None):
        calls.append(verb)
        if payload:
            descriptor.update(payload)
        return {
            "handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
            "status": "editing", "outputs": [],
            "editPolicyReceipt": None if missing else policy_receipt(descriptor),
        }

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    missing = True
    await due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.error_code == "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED" and saved.outputs_json == []
    assert calls == ["POST", "GET", "GET"]


@pytest.mark.parametrize("published", [False, True])
async def test_legacy_lan_snapshot_keeps_intent_hash_and_receipt_contract(
    handoff_env, monkeypatch, published,
):
    from libtv_agent.domain.models import Asset, DownloadArtifact
    from libtv_agent.services.cutflow_handoff import publish_package

    database, settings, store, ids = handoff_env
    row = await shared.enqueue(handoff_env)
    descriptor, calls = {}, []
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        saved.manifest_json = {**saved.manifest_json, "edit_policy": legacy_lan_policy()}
        if published:
            artifact = await session.get(DownloadArtifact, ids["artifact"])
            references = list(await session.scalars(select(Asset).where(
                Asset.parent_task_id == ids["task"], Asset.role == "PRODUCT",
            )))
            descriptor.update(await publish_package(
                saved, artifact, settings, store, product_references=references,
            ))
            saved.manifest_path = f"inbox/{row.handoff_key}/manifest.json"
            saved.manifest_json = {
                **descriptor, "_acceptedManifestSha256": descriptor["manifestSha256"],
            }
            saved.status = "IMPORTING"
        await session.commit()
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    before = (package / "manifest.json").read_bytes() if published else None
    assert saved_policy(await shared.enqueue(handoff_env)) == legacy_lan_policy()

    def remote(_settings, verb, _suffix, payload=None):
        calls.append(verb)
        if payload:
            descriptor.update(payload)
        return {
            "handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
            "status": "editing", "outputs": [],
        }

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    assert calls == (["GET"] if published else ["POST", "GET"])
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["edit_policy"] == legacy_lan_policy()
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        assert manifest["edit_intent"] == {"preserve_source_audio": artifact.has_audio}
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None
    if published:
        assert (package / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("version", ["yiying-ootd/1", "yiying-ootd/2", "yiying-ootd/99"])
async def test_unknown_or_altered_lan_policy_is_rejected_before_transfer(handoff_env, version):
    from libtv_agent.services.cutflow_handoff import handoff_identity

    row = await shared.enqueue(handoff_env)
    policy = legacy_lan_policy() if version == "yiying-ootd/1" else lan_policy()
    policy.update(version=version, grouping="scan_other_jobs")
    row.manifest_json = {**row.manifest_json, "edit_policy": policy}
    with pytest.raises(CutflowHandoffError) as caught:
        handoff_identity(row)
    assert caught.value.code == "CUTFLOW_POLICY_INVALID"
