"""End-to-end multi-source round trip against a mock CUT receiver.

Every other multi-source test stubs the HTTP call or the import step, so a drift
between the bytes the classic side publishes and the keys CUT actually reads
stays invisible. This test runs a real HTTP server that reads the published
package off disk using only the contract CUT reported:

* source keys read as camelCase (``relativePath`` / ``size`` / ``durationSeconds``)
* ``READY`` holds the sha256 of the raw manifest bytes
* ``sources`` is ordered by a contiguous ``order`` 1..N
* outputs live under ``outbox/{taskId}/`` and carry path/sha256/size
* non-terminal statuses omit ``outputs`` entirely

It then drives the real worker through publish -> optional POST wake-up ->
authoritative NAS status -> import and asserts a real video is decoded and
stored.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_cutflow_handoff as shared
from libtv_agent.domain.models import MultiCutflowTask
from libtv_agent.services import cutflow_multi_worker as worker
from libtv_agent.services.cutflow_policy import POLICY_VERSION
from libtv_agent.services.cutflow_protocol import JOB_CONTRACT, STATUS_SCHEMA

handoff_env = shared.handoff_env

CONTRACT = JOB_CONTRACT
TOKEN = "isolated-test-token"
POST_PATH = "/api/integrations/libtv-multisource"


class MockCutReceiver:
    """A receiver that only believes what CUT said it reads."""

    def __init__(self, root: Path, statuses: list[str], output_source: Path | None = None):
        self.root = root
        self.statuses = list(statuses)
        self.output_source = output_source
        self.errors: list[str] = []
        self.posts: list[dict] = []
        self.polls: list[str] = []
        self.accepted_sha: str | None = None

    def _reject(self, code: str) -> dict:
        self.errors.append(code)
        return {"error": {"code": code}}

    def _receipt(self, task_id: str, digest: str | None) -> dict:
        return {
            "state": "accepted", "taskId": task_id, "handoffKey": f"multi-{task_id}",
            "policyVersion": POLICY_VERSION, "manifestSha256": digest,
            "costVerifier": "fixture", "qualityVerifier": "fixture", "editor": "fixture",
        }

    def accept(self, body: dict, auth: str | None) -> dict:
        if auth != f"Bearer {TOKEN}":
            return self._reject("UNAUTHORIZED")
        if body.get("contractVersion") != CONTRACT:
            return self._reject("CONTRACT_MISMATCH")
        task_id = str(body.get("taskId"))
        if body.get("handoffKey") != f"multi-{task_id}":
            return self._reject("HANDOFF_KEY_MISMATCH")

        package = self.root / "inbox" / f"multi-{task_id}"
        manifest_path = package / "manifest.json"
        if not manifest_path.is_file():
            return self._reject("MANIFEST_MISSING")
        raw = manifest_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if body.get("manifestSha256") != digest:
            return self._reject("MANIFEST_HASH_MISMATCH")
        ready = json.loads((package / "READY").read_text(encoding="utf-8"))
        if ready.get("sha256") != digest:
            return self._reject("READY_HASH_MISMATCH")

        manifest = json.loads(raw)
        if body.get("manifest") != manifest:
            return self._reject("MANIFEST_BODY_MISMATCH")
        sources = manifest.get("sources") or []
        if [item.get("order") for item in sources] != list(range(1, len(sources) + 1)):
            return self._reject("SOURCE_ORDER_INVALID")
        for source in sources:
            relative = source.get("relativePath")
            declared_size = source.get("size")
            declared_hash = source.get("sha256")
            if not isinstance(relative, str) or not isinstance(declared_size, int):
                return self._reject("SOURCE_FIELD_MISSING")
            target = package / relative
            if not target.is_file():
                return self._reject("SOURCE_MISSING")
            data = target.read_bytes()
            if len(data) != declared_size:
                return self._reject("SOURCE_SIZE_MISMATCH")
            if hashlib.sha256(data).hexdigest() != declared_hash:
                return self._reject("SOURCE_HASH_MISMATCH")

        self.accepted_sha = digest
        self.posts.append(body)
        out_dir = self.root / "outbox" / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        status = {
            "schemaVersion": STATUS_SCHEMA,
            "revision": 1,
            "contractVersion": CONTRACT,
            "taskId": task_id,
            "handoffKey": f"multi-{task_id}",
            "manifestSha256": digest,
            "batchId": f"batch-{task_id[:8]}",
            "status": "accepted",
            "policyReceipt": self._receipt(task_id, digest),
        }
        (out_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "accepted": True, "taskId": task_id, "notificationOnly": True,
        }

    def poll(self, task_id: str) -> dict:
        status = self.statuses.pop(0) if self.statuses else "queued"
        self.polls.append(status)
        digest = self.accepted_sha
        response = {
            "contractVersion": CONTRACT, "taskId": task_id,
            "handoffKey": f"multi-{task_id}", "manifestSha256": digest,
            "status": status, "policyReceipt": self._receipt(task_id, digest),
        }
        if status not in {"review", "completed"}:
            return response
        assert self.output_source is not None
        out_dir = self.root / "outbox" / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "cut-0001.mp4"
        shutil.copyfile(self.output_source, target)
        data = target.read_bytes()
        response["outputs"] = [{
            "outputId": "cut-0001",
            "relativePath": f"outbox/{task_id}/cut-0001.mp4",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "durationSeconds": 1.0,
        }]
        return response

    def publish_completed(self, task_id: str) -> None:
        assert self.accepted_sha is not None
        assert self.output_source is not None
        out_dir = self.root / "outbox" / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "cut-0001.mp4"
        shutil.copyfile(self.output_source, target)
        data = target.read_bytes()
        response = {
            "schemaVersion": STATUS_SCHEMA,
            "revision": 3,
            "contractVersion": CONTRACT,
            "taskId": task_id,
            "handoffKey": f"multi-{task_id}",
            "manifestSha256": self.accepted_sha,
            "batchId": f"batch-{task_id[:8]}",
            "status": "completed",
            "policyReceipt": self._receipt(task_id, self.accepted_sha),
            "outputs": [{
                "outputId": "cut-0001",
                "relativePath": f"outbox/{task_id}/cut-0001.mp4",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "durationSeconds": 1.0,
            }],
        }
        (out_dir / "status.json").write_text(
            json.dumps(response, ensure_ascii=False),
            encoding="utf-8",
        )


def _handler(receiver: MockCutReceiver) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep pytest output clean
            return

        def _reply(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path != POST_PATH:
                self._reply(404, {"error": {"code": "NOT_FOUND"}})
                return
            result = receiver.accept(body, self.headers.get("Authorization"))
            self._reply(202 if "error" not in result else 400, result)

        def do_GET(self):
            prefix = POST_PATH + "/"
            if not self.path.startswith(prefix):
                self._reply(404, {"error": {"code": "NOT_FOUND"}})
                return
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._reply(401, {"error": {"code": "UNAUTHORIZED"}})
                return
            self._reply(200, receiver.poll(self.path[len(prefix):]))

    return Handler


def _make_video(path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for the end-to-end import check")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=180x320:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=120,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return path


def _open_gates(settings, monkeypatch) -> None:
    settings.cutflow_multi_source_enabled = True
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    settings.cutflow_service_token = TOKEN


async def _create_task(
    database, store, tmp_path, user_id, *, tamper=False, source_count=2, reference_id=None,
):
    sources = []
    for order in range(1, source_count + 1):
        payload = f"clip-{order}-bytes".encode()
        local = tmp_path / f"src-{order}.mp4"
        local.write_bytes(payload)
        key = await store.put_file(local, f"multi/{order}.mp4", "video/mp4")
        sources.append({
            "clipId": f"clip-{order}", "order": order,
            "relativePath": f"sources/{order:02d}.mp4",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "sizeBytes": len(payload), "durationSeconds": 1.0,
            "userId": str(user_id), "taskId": str(uuid.uuid4()),
            "variantId": str(uuid.uuid4()), "artifactId": str(uuid.uuid4()),
            "attemptId": str(uuid.uuid4()),
            # Declared hash wins even when the stored object is tampered with.
            "objectKey": key if not tamper else await _tampered_key(store, tmp_path, order),
        })
    task_id = uuid.uuid4()
    manifest = {"sources": sources}
    if reference_id is not None:
        manifest["referenceBatchTaskId"] = str(reference_id)
    async with database.session_factory() as session:
        session.add(MultiCutflowTask(
            id=task_id, user_id=user_id, idempotency_key=f"key-{task_id}",
            payload_hash="b" * 64, status="QUEUED", sources_json=sources,
            manifest_json=manifest, output_json=[], failure_count=0,
        ))
        await session.commit()
    return task_id


async def _tampered_key(store, tmp_path, order):
    local = tmp_path / f"tampered-{order}.mp4"
    local.write_bytes(b"tampered-bytes")
    return await store.put_file(local, f"multi/tampered-{order}.mp4", "video/mp4")


async def _tick(database, settings, store, task_id):
    """Run one worker tick and return a snapshot taken before the backoff reset."""
    assert await worker.tick_multi(database, settings, store)
    async with database.session_factory() as session:
        row = await session.get(MultiCutflowTask, task_id)
        assert row is not None
        snapshot = SimpleNamespace(
            status=row.status, error_code=row.error_code,
            failure_count=row.failure_count, next_run_at=row.next_run_at,
            output_json=list(row.output_json or []), lease_token=row.lease_token,
            manifest_path=row.manifest_path,
        )
        row.next_run_at = None  # simulate the retry backoff expiring
        await session.commit()
        return snapshot


def _serve(receiver: MockCutReceiver):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(receiver))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True], ids=["first", "repeat"])
async def test_round_trip_publishes_posts_polls_then_imports(
    handoff_env, tmp_path, monkeypatch, repeat,
):
    database, settings, store, ids = handoff_env
    _open_gates(settings, monkeypatch)
    output_source = _make_video(tmp_path / "cut-output.mp4")
    reference_id = uuid.uuid4() if repeat else None
    task_id = await _create_task(
        database, store, tmp_path, ids["user"], source_count=3, reference_id=reference_id,
    )

    receiver = MockCutReceiver(settings.cutflow_handoff_root, ["editing", "completed"],
                               output_source)
    server = _serve(receiver)
    try:
        settings.cutflow_base_url = f"http://127.0.0.1:{server.server_port}"

        # One tick publishes the package, sends the optional wake-up and waits
        # for CUT's authoritative NAS status.
        first = await _tick(database, settings, store, task_id)
        assert first.status == "READY", (first.status, first.error_code)
        assert receiver.posts, f"POST rejected: {receiver.errors}"
        assert not any(
            key.startswith("_") for key in receiver.posts[0]["manifest"]
        )
        assert len(receiver.posts[0]["manifest"]["sources"]) == 3
        assert [
            source["order"] for source in receiver.posts[0]["manifest"]["sources"]
        ] == [1, 2, 3]
        assert first.manifest_path == f"inbox/multi-{task_id}/manifest.json"

        second = await _tick(database, settings, store, task_id)
        assert second.status == "ACCEPTED"

        receiver.publish_completed(str(task_id))
        final = await _tick(database, settings, store, task_id)
    finally:
        server.shutdown()
        server.server_close()

    assert receiver.errors == []
    assert len(receiver.posts) == 1
    assert receiver.polls == []
    assert final.status == "PENDING_REVIEW"
    assert final.failure_count == 0
    assert final.lease_token is None

    saved = final.output_json
    assert len(saved) == 1
    assert saved[0]["id"] == "cut-0001"
    assert saved[0]["task_id"] == str(task_id)
    assert saved[0]["manifest_sha256"] == receiver.accepted_sha
    assert saved[0]["review_status"] == "PENDING"
    assert saved[0]["width"] == 180 and saved[0]["height"] == 320
    assert await store.exists(saved[0]["object_key"])

    # The package on disk must satisfy a camelCase reader as well as the
    # documented LAN /1 snake_case one; this is the drift this test exists for.
    package = settings.cutflow_handoff_root / "inbox" / f"multi-{task_id}"
    manifest = json.loads((package / "manifest.json").read_bytes())
    if repeat:
        assert manifest["referenceBatchTaskId"] == str(reference_id)
    else:
        assert "referenceBatchTaskId" not in manifest
    for source in manifest["sources"]:
        assert source["relativePath"] == source["relative_path"]
        assert source["size"] == source["size_bytes"]
        assert source["durationSeconds"] == source["duration_seconds"]
    assert hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest() == (
        receiver.accepted_sha
    )


@pytest.mark.asyncio
async def test_rejected_submission_stays_retryable_and_never_completes(
    handoff_env, tmp_path, monkeypatch,
):
    database, settings, store, ids = handoff_env
    _open_gates(settings, monkeypatch)
    task_id = await _create_task(database, store, tmp_path, ids["user"])

    receiver = MockCutReceiver(settings.cutflow_handoff_root, ["completed"])
    server = _serve(receiver)
    try:
        settings.cutflow_base_url = f"http://127.0.0.1:{server.server_port}"
        settings.cutflow_service_token = "wrong-token"

        row = await _tick(database, settings, store, task_id)
        assert row.failure_count == 0
        assert row.error_code is None
        assert row.status == "READY"
        assert row.output_json == []
        assert row.next_run_at is not None
    finally:
        server.shutdown()
        server.server_close()

    assert receiver.errors == ["UNAUTHORIZED"]
    assert receiver.posts == []


@pytest.mark.asyncio
async def test_tampered_source_never_reaches_cut(handoff_env, tmp_path, monkeypatch):
    database, settings, store, ids = handoff_env
    _open_gates(settings, monkeypatch)
    task_id = await _create_task(database, store, tmp_path, ids["user"], tamper=True)

    receiver = MockCutReceiver(settings.cutflow_handoff_root, [])
    server = _serve(receiver)
    try:
        settings.cutflow_base_url = f"http://127.0.0.1:{server.server_port}"

        row = await _tick(database, settings, store, task_id)
        assert row.status == "QUEUED"
        assert row.error_code == "VIDEO_HASH_MISMATCH"
        assert row.manifest_path is None
    finally:
        server.shutdown()
        server.server_close()

    assert receiver.posts == []
    assert not (settings.cutflow_handoff_root / "inbox" / f"multi-{task_id}").exists()
