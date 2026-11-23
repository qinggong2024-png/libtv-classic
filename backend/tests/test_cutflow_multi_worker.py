import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import URLError

import pytest
import test_cutflow_handoff as shared
from libtv_agent.domain.models import MultiCutflowTask
from libtv_agent.runner import cutflow_worker
from libtv_agent.services import cutflow_handoff
from libtv_agent.services import cutflow_multi_worker as worker
from libtv_agent.services.cutflow_handoff import CutflowHandoffError
from libtv_agent.services.cutflow_multi_adapter import CONTRACT_VERSION
from libtv_agent.services.cutflow_policy import POLICY_VERSION
from sqlalchemy import select

handoff_env = shared.handoff_env
MANIFEST_HASH = "a" * 64


def test_worker_requires_explicit_lan_configuration(handoff_env):
    _database, settings, _store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_multi_source_enabled = False
    settings.cutflow_nas_status_enabled = True
    assert not worker.multi_worker_ready(settings)
    settings.cutflow_multi_source_enabled = True
    assert worker.multi_worker_ready(settings)
    settings.cutflow_nas_status_enabled = False
    assert not worker.multi_worker_ready(settings)


async def create_multi_task(env, *, status="EDITING", output_json=None):
    database, _settings, _store, ids = env
    task_id = uuid.uuid4()
    task = MultiCutflowTask(
        id=task_id, user_id=ids["user"], idempotency_key=f"key-{uuid.uuid4()}",
        payload_hash="b" * 64, status=status, sources_json=[],
        manifest_json={"manifestSha256": MANIFEST_HASH, "taskId": str(task_id),
                       "_acceptedManifestSha256": MANIFEST_HASH},
        manifest_path=f"inbox/multi-{task_id}/manifest.json", output_json=output_json or [],
        lease_token="lease-token", failure_count=0,
    )
    async with database.session_factory() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
    return task


def cut_response(task, *, status="unknown", outputs=None, task_id=None, digest=MANIFEST_HASH):
    response = {
        "taskId": str(task_id or task.id),
        "handoffKey": f"multi-{task_id or task.id}", "manifestSha256": digest,
        "status": status,
        "policyReceipt": {"state": "accepted", "taskId": str(task_id or task.id),
                           "handoffKey": f"multi-{task_id or task.id}",
                           "policyVersion": POLICY_VERSION,
                           "manifestSha256": digest},
    }
    if status in {"review", "completed"}:
        response["outputs"] = outputs or []
    return response


def output_payload(task, *, relative_path=None):
    return {"outputId": "out-1", "relativePath": relative_path or f"outbox/{task.id}/edit.mp4",
            "size": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}


@pytest.mark.parametrize(("remote", "local"), [
    ("queued", "ACCEPTED"), ("accepted", "ACCEPTED"),
    ("uploading", "IMPORTING"), ("editing", "EDITING"),
    ("review", "PENDING_REVIEW"), ("completed", "PENDING_REVIEW"),
    ("failed", "FAILED"), ("unknown", "UNKNOWN"),
])
def test_cut_status_mapping_is_explicit(handoff_env, remote, local):
    task = MultiCutflowTask(id=uuid.uuid4(), manifest_json={"manifestSha256": MANIFEST_HASH})
    outputs = [output_payload(task)] if remote in {"review", "completed"} else []
    assert worker.normalize_multi_status(
        task, cut_response(task, status=remote, outputs=outputs),
    )["status"] == local


@pytest.mark.asyncio
async def test_unknown_status_repolls_same_task_without_submission(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env)
    queried = []

    def poll(_settings, task_id):
        queried.append(str(task_id))
        return cut_response(task, status="unknown")

    monkeypatch.setattr(worker, "cut_multi_status_request", poll)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.scalar(select(MultiCutflowTask).where(MultiCutflowTask.id == task.id))
        assert saved.status == "UNKNOWN"
        assert saved.failure_count == 0
        assert saved.next_run_at is not None
        assert saved.error_code == "CUTFLOW_STATUS_UNKNOWN"
        assert saved.lease_token is None
    assert queried == [str(task.id)]


@pytest.mark.asyncio
async def test_network_error_retries_without_marking_complete(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env)
    calls = []

    def poll(_settings, task_id):
        calls.append(str(task_id))
        raise URLError("fixture offline")

    monkeypatch.setattr(worker, "cut_multi_status_request", poll)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.failure_count == 1
        assert saved.next_run_at is not None
        assert saved.output_json == []
    assert calls == [str(task.id)]


@pytest.mark.parametrize("path", [
    "../other/edit.mp4", "outbox/other-task/edit.mp4", "outbox/{task}/../edit.mp4",
    "outbox/{task}/nested/edit.mp4", "C:/outside.mp4", "/outside.mp4",
])
def test_output_path_must_be_directly_under_task_outbox(tmp_path, path):
    task_id = uuid.uuid4()
    unsafe = path.format(task=task_id)
    with pytest.raises(CutflowHandoffError):
        worker.validate_multi_output_paths(task_id, [{"relative_path": unsafe}], tmp_path)


@pytest.mark.asyncio
async def test_manifest_hash_and_task_identity_mismatch_never_import(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env)
    imported = []
    monkeypatch.setattr(worker, "cut_multi_status_request", lambda *_: cut_response(
        task, status="completed", outputs=[output_payload(task)], digest="c" * 64,
    ))
    monkeypatch.setattr(worker, "import_multi_outputs", lambda *args: imported.append(args))
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.output_json == []
        assert saved.error_code == "INVALID_RESULT"
    assert imported == []

    async with database.session_factory() as session:
        persisted = await session.get(MultiCutflowTask, task.id)
        persisted.lease_token = "lease-token"
        await session.commit()
        await session.refresh(persisted)
        task = persisted
    monkeypatch.setattr(worker, "cut_multi_status_request", lambda *_: cut_response(
        task, status="completed", outputs=[output_payload(task)], task_id=uuid.uuid4(),
    ))
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.output_json == []


def test_missing_nas_status_is_absence_but_nas_io_failure_is_not(handoff_env):
    _database, settings, _store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = MultiCutflowTask(id=uuid.uuid4())
    status_path = settings.cutflow_handoff_root / "outbox" / str(task.id) / "status.json"
    assert worker.read_multi_nas_status(task, settings) is None

    status_path.mkdir(parents=True)
    with pytest.raises(CutflowHandoffError) as error:
        worker.read_multi_nas_status(task, settings)
    assert error.value.code == "CUTFLOW_NAS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_completed_without_explicit_output_cannot_complete(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env)
    monkeypatch.setattr(worker, "cut_multi_status_request", lambda *_: cut_response(
        task, status="completed", outputs=[],
    ))
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.output_json == []
        assert saved.error_code == "INVALID_RESULT"


@pytest.mark.asyncio
async def test_verified_result_persists_atomically_for_task_owner(handoff_env, monkeypatch):
    database, settings, store, ids = handoff_env
    task = await create_multi_task(handoff_env)
    response = cut_response(task, status="review", outputs=[output_payload(task)])
    monkeypatch.setattr(worker, "cut_multi_status_request", lambda *_: response)
    captured = {}

    async def import_fixture(cut_task, result, _settings, _store):
        captured["task_id"] = str(cut_task.id)
        captured["owner_id"] = cut_task.user_id
        return [{"id": "out-1", "object_key": "private/result.mp4",
                 "sha256": response["outputs"][0]["sha256"], "size_bytes": 7,
                 "review_status": "PENDING", "task_id": str(cut_task.id),
                 "manifest_sha256": MANIFEST_HASH}]

    monkeypatch.setattr(worker, "import_multi_outputs", import_fixture)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "PENDING_REVIEW"
        assert saved.output_json[0]["task_id"] == str(task.id)
        assert saved.output_json[0]["manifest_sha256"] == MANIFEST_HASH
        assert saved.lease_token is None
    assert captured == {"task_id": str(task.id), "owner_id": ids["user"]}


@pytest.mark.asyncio
async def test_unified_task_publishes_notifies_once_and_waits_for_nas_status(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env, status="QUEUED")
    task.manifest_path = None
    task.manifest_json = {"sources": []}
    async with database.session_factory() as session:
        stored = await session.get(MultiCutflowTask, task.id)
        stored.manifest_path = None
        stored.manifest_json = {"sources": []}
        stored.status = "QUEUED"
        await session.commit()
        await session.refresh(stored)
        task = stored

    descriptor = {
        "taskId": str(task.id), "manifestSha256": MANIFEST_HASH,
        "manifestRelativePath": f"inbox/multi-{task.id}/manifest.json",
        "contractVersion": CONTRACT_VERSION, "schema_version": CONTRACT_VERSION,
        "sources": [], "edit_policy": {},
    }
    monkeypatch.setattr(worker, "publish_multi_package", lambda *_: async_value(
        (descriptor, MANIFEST_HASH),
    ))
    posted = []

    def post(_settings, payload):
        posted.append(payload)
        return cut_response(task, status="queued")

    monkeypatch.setattr(worker, "cut_multi_submit_request", post)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.manifest_path == f"inbox/multi-{task.id}/manifest.json"
        assert saved.manifest_json["_statusAuthority"] == "nas"
        assert saved.manifest_json["_lastNotificationAt"]
        assert saved.manifest_json["_lastNotification"]["outcome"] == "seen"
        assert saved.next_run_at is not None
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_legacy_manifest_fails_closed_without_a_mixed_contract_post(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    task = await create_multi_task(handoff_env)
    legacy_manifest = {
        **task.manifest_json,
        "contractVersion": "libtv-cut-lan-multi/1",
        "schema_version": "libtv-cut-lan-multi/1",
    }
    legacy_manifest.pop("_acceptedManifestSha256", None)
    async with database.session_factory() as session:
        stored = await session.get(MultiCutflowTask, task.id)
        stored.manifest_json = legacy_manifest
        await session.commit()
        await session.refresh(stored)
        task = stored

    posted = []
    monkeypatch.setattr(
        worker,
        "cut_multi_submit_request",
        lambda *_args: posted.append(True),
    )
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "FAILED"
        assert saved.error_code == "CUTFLOW_CONTRACT_UPGRADE_REQUIRED"
        assert saved.next_run_at is None
        assert saved.lease_token is None
    assert posted == []


async def async_value(value):
    return value


@pytest.mark.asyncio
async def test_failed_output_validation_leaves_task_outputs_unchanged(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    previous = [{"id": "old", "task_id": "old-task", "manifest_sha256": "f" * 64}]
    task = await create_multi_task(handoff_env, output_json=previous)
    monkeypatch.setattr(worker, "cut_multi_status_request", lambda *_: cut_response(
        task, status="completed", outputs=[output_payload(task)],
    ))

    async def reject(*_args):
        raise CutflowHandoffError("INVALID_RESULT", "fixture validation failed")

    monkeypatch.setattr(worker, "import_multi_outputs", reject)
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.output_json == previous
        assert saved.next_run_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["hash", "size", "decode"])
async def test_multi_import_reuses_hash_size_and_full_decode_guards(
    handoff_env, monkeypatch, failure,
):
    _database, settings, store, ids = handoff_env
    task_id = uuid.uuid4()
    task = SimpleNamespace(id=task_id, user_id=ids["user"],
                           manifest_json={"manifestSha256": MANIFEST_HASH}, output_json=[])
    content = b"valid fixture bytes"
    filename = "edit.mp4"
    task_outbox = settings.cutflow_handoff_root / "outbox" / str(task_id)
    task_outbox.mkdir(parents=True)
    (task_outbox / filename).write_bytes(content)
    output = {"id": "out-1", "relative_path": f"outbox/{task_id}/{filename}",
              "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    if failure == "hash":
        output["sha256"] = "0" * 64
    elif failure == "size":
        output["size_bytes"] += 1
    else:
        def bad_decode(_path):
            raise CutflowHandoffError("INVALID_RESULT", "fixture decode failed")
        monkeypatch.setattr(cutflow_handoff, "_decode_check", bad_decode)

    result = {"id": str(task_id), "manifestSha256": MANIFEST_HASH,
              "status": "PENDING_REVIEW", "outputs": [output]}
    with pytest.raises(CutflowHandoffError):
        await worker.import_multi_outputs(task, result, settings, store)


def test_get_status_endpoint_is_task_scoped_and_never_posts(handoff_env, monkeypatch):
    _database, settings, _store, _ids = handoff_env
    task_id = uuid.uuid4()
    seen = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, _limit):
            return b'{"status":"unknown"}'

    class Opener:
        def open(self, request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["timeout"] = timeout
            return Response()

    monkeypatch.setattr(worker, "build_opener", lambda *_: Opener())
    assert worker.cut_multi_status_request(settings, task_id) == {"status": "unknown"}
    assert seen == {
        "url": f"{settings.cutflow_base_url}/api/integrations/libtv-multisource/{task_id}",
        "method": "GET", "timeout": settings.cutflow_request_timeout_seconds,
    }


@pytest.mark.asyncio
async def test_worker_dispatch_requires_lan_configuration(handoff_env):
    database, settings, store, _ids = handoff_env
    settings.cutflow_multi_source_enabled = True
    assert not worker.multi_worker_ready(settings)
    assert not await worker.tick_multi(database, settings, store)


@pytest.mark.asyncio
async def test_single_source_worker_remains_idle_without_a_queued_handoff(handoff_env):
    database, settings, store, _ids = handoff_env
    assert await cutflow_worker.tick(database, settings, store) is False


@pytest.mark.asyncio
async def test_publish_multi_package_copies_sources_then_publishes_ready_last(handoff_env):
    _database, settings, store, ids = handoff_env
    task_id = uuid.uuid4()
    source_path = settings.artifact_root / "source-1.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"source fixture")
    object_key = await store.put_file(source_path, "multi/source-1.mp4", "video/mp4")
    source_digest = hashlib.sha256(b"source fixture").hexdigest()
    task = SimpleNamespace(
        id=task_id, user_id=ids["user"], created_at=datetime.now(UTC),
        sources_json=[{
            "clipId": "clip-1", "order": 1, "relativePath": "sources/01.mp4",
            "sha256": source_digest, "sizeBytes": len(b"source fixture"),
            "durationSeconds": 2.0, "userId": str(ids["user"]),
            "taskId": "source-task", "variantId": "source-variant",
            "artifactId": "source-artifact", "attemptId": "source-attempt",
            "objectKey": object_key,
        }],
    )

    descriptor, digest = await worker.publish_multi_package(task, settings, store)
    package = settings.cutflow_handoff_root / "inbox" / f"multi-{task_id}"
    raw_manifest = (package / "manifest.json").read_bytes()
    assert hashlib.sha256(raw_manifest).hexdigest() == digest
    assert json.loads((package / "READY").read_text()) == {"sha256": digest}
    assert (package / "sources" / "01.mp4").read_bytes() == b"source fixture"
    assert descriptor["manifestSha256"] == digest
    assert descriptor["sources"][0]["relative_path"] == "sources/01.mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_ready", "wrong_ready", "changed_source"])
async def test_existing_cut_package_repairs_only_a_missing_ready_after_media_check(
    handoff_env, damage,
):
    _database, settings, store, ids = handoff_env
    task_id = uuid.uuid4()
    source_path = settings.artifact_root / "ready-recovery-source.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"recovery media fixture")
    object_key = await store.put_file(
        source_path, "multi/ready-recovery-source.mp4", "video/mp4",
    )
    source_digest = hashlib.sha256(b"recovery media fixture").hexdigest()
    task = SimpleNamespace(
        id=task_id, user_id=ids["user"], created_at=datetime.now(UTC),
        sources_json=[{
            "clipId": "clip-1", "order": 1, "relativePath": "sources/01.mp4",
            "sha256": source_digest, "sizeBytes": len(b"recovery media fixture"),
            "durationSeconds": 2.0, "userId": str(ids["user"]),
            "taskId": "original-parent", "variantId": "original-variant",
            "artifactId": "original-artifact", "attemptId": "original-attempt",
            "objectKey": object_key,
        }],
    )
    first, original_hash = await worker.publish_multi_package(task, settings, store)
    final = settings.cutflow_handoff_root / "inbox" / f"multi-{task_id}"
    original_manifest = (final / "manifest.json").read_bytes()
    ready = final / "READY"
    if damage == "missing_ready":
        ready.unlink()
        restored, restored_hash = await worker.publish_multi_package(
            task, settings, store,
        )
        assert restored_hash == original_hash
        assert restored == first
        assert json.loads(ready.read_text(encoding="utf-8")) == {
            "sha256": original_hash,
        }
    else:
        if damage == "wrong_ready":
            ready.write_text('{"sha256":"tampered"}', encoding="utf-8")
        else:
            ready.unlink()
            (final / "sources" / "01.mp4").write_bytes(b"altered media")
        with pytest.raises(CutflowHandoffError) as caught:
            await worker.publish_multi_package(task, settings, store)
        assert caught.value.code == (
            "IMMUTABLE_RESULT_CHANGED" if damage == "wrong_ready"
            else "CUTFLOW_SOURCE_INVALID"
        )
        if damage == "changed_source":
            assert not ready.exists(), "never publish READY for corrupted media"
    assert (final / "manifest.json").read_bytes() == original_manifest


def test_post_endpoint_uses_existing_auth_and_multisource_route(handoff_env, monkeypatch):
    _database, settings, _store, _ids = handoff_env
    seen = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, _limit):
            return b'{"accepted":true}'

    class Opener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, method=request.get_method(),
                         auth=request.get_header("Authorization"), timeout=timeout,
                         body=json.loads(request.data))
            return Response()

    monkeypatch.setattr(worker, "build_opener", lambda *_: Opener())
    payload = {"taskId": str(uuid.uuid4()), "idempotencyKey": "stable"}
    assert worker.cut_multi_submit_request(settings, payload) == {"accepted": True}
    assert seen == {
        "url": f"{settings.cutflow_base_url}/api/integrations/libtv-multisource",
        "method": "POST", "auth": f"Bearer {settings.cutflow_service_token}",
        "timeout": settings.cutflow_request_timeout_seconds, "body": payload,
    }
