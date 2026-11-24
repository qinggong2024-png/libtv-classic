import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

import pytest
import test_cutflow_handoff as shared
from libtv_agent.domain.models import CutflowHandoff, MultiCutflowTask
from libtv_agent.services import cutflow_multi_worker as worker
from libtv_agent.services import cutflow_receipts
from libtv_agent.services.cutflow_handoff import CutflowHandoffError
from libtv_agent.services.cutflow_policy import POLICY_VERSION
from libtv_agent.services.cutflow_protocol import (
    JOB_CONTRACT,
    STATUS_SCHEMA,
    RevisionCheck,
    check_status_revision,
    remember_status_revision,
)

handoff_env = shared.handoff_env
MANIFEST_HASH = "a" * 64


def status_payload(task_id, *, revision, status="completed", outputs=None):
    response = {
        "schemaVersion": STATUS_SCHEMA,
        "revision": revision,
        "taskId": str(task_id),
        "handoffKey": f"multi-{task_id}",
        "manifestSha256": MANIFEST_HASH,
        "status": status,
        "batchId": f"batch-{str(task_id)[:8]}",
        "policyReceipt": {
            "state": "accepted",
            "taskId": str(task_id),
            "handoffKey": f"multi-{task_id}",
            "policyVersion": POLICY_VERSION,
            "manifestSha256": MANIFEST_HASH,
        },
    }
    if status in {"review", "completed"}:
        response["outputs"] = outputs or [{
            "outputId": "out-1",
            "relativePath": f"outbox/{task_id}/out-1.mp4",
            "size": 7,
            "sha256": "b" * 64,
        }]
    return response


async def create_v2_task(env, *, revision=None):
    database, _settings, _store, ids = env
    task_id = uuid.uuid4()
    manifest = {
        "contractVersion": JOB_CONTRACT,
        "schema_version": JOB_CONTRACT,
        "statusSchema": STATUS_SCHEMA,
        "taskId": str(task_id),
        "handoffKey": f"multi-{task_id}",
        "idempotencyKey": f"{JOB_CONTRACT}:{task_id}",
        "manifestSha256": MANIFEST_HASH,
        "manifestRelativePath": f"inbox/multi-{task_id}/manifest.json",
        "edit_policy": {},
        "sources": [],
    }
    if revision is not None:
        manifest.update(
            _lastCutRevision=revision,
            _lastCutSnapshotSha256="c" * 64,
        )
    task = MultiCutflowTask(
        id=task_id,
        user_id=ids["user"],
        idempotency_key=f"key-{task_id}",
        payload_hash="d" * 64,
        status="READY",
        sources_json=[],
        manifest_json=manifest,
        manifest_path=f"inbox/multi-{task_id}/manifest.json",
        output_json=[],
        failure_count=0,
        lease_token="lease-token",
    )
    async with database.session_factory() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
    return task


def write_status(settings, task, payload):
    directory = settings.cutflow_handoff_root / "outbox" / str(task.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_revision_guard_is_monotonic_and_detects_same_revision_rewrite():
    manifest = {}
    first = status_payload(uuid.uuid4(), revision=1, status="processing", outputs=[])
    checked = check_status_revision(manifest, first, required=True)
    assert checked == RevisionCheck(1, checked.fingerprint, False)
    remembered = remember_status_revision(manifest, checked)
    assert remembered["_lastCutRevision"] == 1

    same = status_payload(
        uuid.UUID(first["taskId"]),
        revision=1,
        status="processing",
        outputs=[],
    )
    assert check_status_revision(remembered, same, required=True).stale is False

    changed = {**same, "status": "failed", "error": {"code": "X"}}
    with pytest.raises(CutflowHandoffError, match="相同 revision"):
        check_status_revision(remembered, changed, required=True)

    newer_check = check_status_revision(
        remembered,
        status_payload(uuid.UUID(first["taskId"]), revision=3, status="processing"),
        required=True,
    )
    newer_manifest = remember_status_revision(remembered, newer_check)
    assert check_status_revision(
        newer_manifest,
        status_payload(uuid.UUID(first["taskId"]), revision=2, status="processing"),
        required=True,
    ).stale is True


@pytest.mark.asyncio
async def test_v2_reads_nas_status_and_does_not_use_http_get(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    write_status(settings, task, status_payload(task.id, revision=2))

    async def import_fixture(cut_task, result, _settings, _store):
        return [{
            "id": result["outputs"][0]["id"],
            "sha256": result["outputs"][0]["sha256"],
            "object_key": "private/result.mp4",
            "size_bytes": 7,
            "review_status": "PENDING",
            "task_id": str(cut_task.id),
            "manifest_sha256": MANIFEST_HASH,
        }]

    monkeypatch.setattr(worker, "import_multi_outputs", import_fixture)
    monkeypatch.setattr(
        worker,
        "cut_multi_status_request",
        lambda *_args: (_ for _ in ()).throw(AssertionError("HTTP GET must not run")),
    )
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "PENDING_REVIEW"
        assert saved.manifest_json["_lastCutRevision"] == 2
        assert saved.manifest_json["_cutBatchId"] == f"batch-{str(task.id)[:8]}"
        assert saved.output_json[0]["id"] == "out-1"


@pytest.mark.asyncio
async def test_v2_processing_with_early_outputs_waits_then_imports_on_completed(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    # This is the real-world CUT regression: a fully rendered output is
    # prematurely advertised while the V2 task remains "processing".
    early = status_payload(task.id, revision=3, status="processing")
    early["outputs"] = [{
        "outputId": "out-1", "relativePath": f"outbox/{task.id}/out-1.mp4",
        "size": 7, "sha256": "b" * 64,
        "reviewStatus": "PENDING",
    }]
    write_status(settings, task, early)
    async with database.session_factory() as session:
        stored = await session.get(MultiCutflowTask, task.id)
        stored.status = "EDITING"
        stored.failure_count = 14
        stored.error_code = "INVALID_RESULT"
        stored.error_message = "previous compatibility error"
        await session.commit()
        await session.refresh(stored)
        task = stored

    imported = []

    async def import_fixture(cut_task, result, _settings, _store):
        imported.append(result)
        return [{
            "id": result["outputs"][0]["id"],
            "sha256": result["outputs"][0]["sha256"],
            "object_key": "private/result.mp4", "size_bytes": 7,
            "review_status": "PENDING", "task_id": str(cut_task.id),
            "manifest_sha256": MANIFEST_HASH,
        }]

    monkeypatch.setattr(worker, "import_multi_outputs", import_fixture)
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.failure_count == 0
        assert saved.error_code is None
        assert saved.error_message is None
        assert saved.output_json == []
        assert saved.manifest_json["_lastCutRevision"] == 3
        assert saved.next_run_at is not None
        saved.lease_token = "lease-next"
        await session.commit()
        await session.refresh(saved)
        task = saved
    assert imported == []

    write_status(settings, task, status_payload(task.id, revision=4, status="completed"))
    await worker.process_multi_task(database, settings, store, task, "lease-next")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "PENDING_REVIEW"
        assert saved.manifest_json["_lastCutRevision"] == 4
        assert saved.failure_count == 0
        assert saved.error_code is None
        assert len(saved.output_json) == 1
    assert len(imported) == 1


@pytest.mark.asyncio
async def test_v2_overdue_acceptance_is_visible_and_same_job_recovers(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        saved.manifest_json = {
            **saved.manifest_json,
            "_readyAt": (datetime.now(UTC) - timedelta(seconds=91)).isoformat(),
        }
        await session.commit()
        await session.refresh(saved)
        task = saved
    submissions = []

    def unreachable(_settings, payload):
        submissions.append(payload["taskId"])
        raise URLError(ConnectionRefusedError(10061, "private URL"))

    monkeypatch.setattr(worker, "cut_multi_submit_request", unreachable)
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.error_code == "CUTFLOW_STATUS_MISSING"
        notice = saved.manifest_json["_lastNotification"]
        assert notice["transportError"] == "ConnectionRefusedError"
        assert notice["transportErrno"] == 10061
        assert "private URL" not in json.dumps(notice)
        assert saved.failure_count == 0
        saved.lease_token = "lease-recovered"
        await session.commit()
        await session.refresh(saved)
        task = saved
    write_status(settings, task, status_payload(task.id, revision=1, status="accepted"))
    await worker.process_multi_task(database, settings, store, task, "lease-recovered")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "ACCEPTED"
        assert saved.error_code is None
        assert saved.manifest_json["_firstCutReceiptObservedAt"]
    assert submissions == [str(task.id)]


def test_v2_notification_uses_short_timeout(handoff_env, monkeypatch):
    _database, settings, _store, _ids = handoff_env
    settings.cutflow_request_timeout_seconds = 90
    seen = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, _limit):
            return b'{}'

    class Opener:
        def open(self, request, timeout):
            seen.append(timeout)
            return Response()

    monkeypatch.setattr(worker, "build_opener", lambda *_: Opener())
    worker.cut_multi_submit_request(settings, {"notificationOnly": True})
    worker.cut_multi_submit_request(settings, {})
    assert seen == [5, 90]


@pytest.mark.asyncio
async def test_v2_notification_failure_keeps_task_ready_without_failure_count(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)

    def notification_failure(*_args, **_kwargs):
        raise ConnectionError("notification endpoint unavailable")

    monkeypatch.setattr(worker, "cut_multi_submit_request", notification_failure)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.failure_count == 0
        assert saved.error_code is None
        assert saved.next_run_at is not None
        assert saved.manifest_json["_statusAuthority"] == "nas"
        assert saved.manifest_json["_lastNotification"]["outcome"] == "failed"
        assert saved.manifest_json["_lastNotification"]["errorCode"] == "ConnectionError"


@pytest.mark.asyncio
async def test_v2_http_timeout_reconciles_cut_acceptance_without_second_submission(
    handoff_env, monkeypatch,
):
    """A timeout is not a rejection: the original CUT task identity survives."""
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    submissions = []

    def delayed_receipt(_settings, payload):
        submissions.append(payload["taskId"])
        raise TimeoutError("simulated HTTP response timeout after 90 seconds")

    monkeypatch.setattr(worker, "cut_multi_submit_request", delayed_receipt)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.failure_count == 0
        assert saved.next_run_at is not None
        assert saved.manifest_json["_lastNotification"]["errorCode"] == "TimeoutError"
        assert saved.manifest_json["taskId"] == str(task.id)
        saved.lease_token = "lease-after-timeout"
        await session.commit()
        await session.refresh(saved)
        task = saved

    write_status(settings, task, status_payload(task.id, revision=1, status="accepted"))
    await worker.process_multi_task(database, settings, store, task, "lease-after-timeout")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "ACCEPTED"
        assert saved.failure_count == 0
        assert saved.error_code is None
        assert saved.manifest_json["_lastCutRevision"] == 1
    assert submissions == [str(task.id)]


@pytest.mark.asyncio
async def test_v2_repeated_poll_keeps_nas_polling_without_http_notification_flood(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    submissions = []

    def slow_cut(_settings, payload):
        submissions.append(payload["taskId"])
        raise TimeoutError("CUT may have accepted while response was lost")

    monkeypatch.setattr(worker, "cut_multi_submit_request", slow_cut)
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        original_notification = saved.manifest_json["_lastNotification"]
        assert original_notification["outcome"] == "failed"
        saved.lease_token = "lease-second"
        await session.commit()
        await session.refresh(saved)
        task = saved
    await worker.process_multi_task(database, settings, store, task, "lease-second")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.manifest_json["_lastNotification"] == original_notification
        assert saved.failure_count == 0
        assert saved.next_run_at is not None
    assert submissions == [str(task.id)]


@pytest.mark.asyncio
async def test_v2_cut_acceptance_is_not_resubmitted_when_nas_snapshot_disappears(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    write_status(settings, task, status_payload(task.id, revision=1, status="accepted"))
    submissions = []
    monkeypatch.setattr(
        worker, "cut_multi_submit_request",
        lambda *_: submissions.append("duplicate"),
    )
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "ACCEPTED"
        saved.lease_token = "lease-snapshot-missing"
        await session.commit()
        await session.refresh(saved)
        task = saved

    status_file = settings.cutflow_handoff_root / "outbox" / str(task.id) / "status.json"
    status_file.unlink()
    await worker.process_multi_task(database, settings, store, task, "lease-snapshot-missing")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "ACCEPTED"
        assert saved.error_code == "CUTFLOW_STATUS_MISSING"
        assert saved.next_run_at is not None
    assert submissions == []


@pytest.mark.asyncio
async def test_http_413_is_visible_but_does_not_resubmit_or_lose_nas_recovery(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)
    calls = []

    def reject_oversized_notification(*_args):
        calls.append("post")
        raise worker.MultiControlHTTPError(413, "BODY_TOO_LARGE")

    monkeypatch.setattr(worker, "cut_multi_submit_request", reject_oversized_notification)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.error_code == "CUTFLOW_NOTIFICATION_TOO_LARGE"
        assert "413" in saved.error_message
        assert saved.failure_count == 0
        assert saved.manifest_json["_lastNotification"]["reason"] == "BODY_TOO_LARGE"
        assert saved.manifest_json["_lastNotification"]["httpStatus"] == 413
        assert saved.next_run_at is not None
        saved.lease_token = "lease-recovered"
        await session.commit()
        await session.refresh(saved)
        task = saved

    # Retry the same leased task before CUT's NAS receipt appears: the POST
    # must be throttled, but NAS is still checked every regular scan.
    await worker.process_multi_task(database, settings, store, task, "lease-recovered")
    assert calls == ["post"]
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.error_code == "CUTFLOW_NOTIFICATION_TOO_LARGE"
        assert saved.next_run_at is not None
        saved.lease_token = "lease-recovered"
        await session.commit()
        await session.refresh(saved)
        task = saved

    write_status(settings, task, status_payload(task.id, revision=1, status="accepted"))
    await worker.process_multi_task(database, settings, store, task, "lease-recovered")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "ACCEPTED"
        assert saved.error_code is None
        assert saved.error_message is None
        assert saved.failure_count == 0
    assert calls == ["post"]


@pytest.mark.asyncio
async def test_v2_rejection_keeps_ready_and_surfaces_safe_cut_reason(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)

    def reject(*_args, **_kwargs):
        raise worker.MultiControlHTTPError(409, "INVALID_PRODUCT_REFERENCE")

    monkeypatch.setattr(worker, "cut_multi_submit_request", reject)
    await worker.process_multi_task(database, settings, store, task, "lease-token")
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "READY"
        assert saved.failure_count == 0
        assert saved.error_code == "CUTFLOW_IMPORT_REJECTED"
        assert "INVALID_PRODUCT_REFERENCE" in saved.error_message
        assert saved.manifest_json["_lastNotification"]["reason"] == "INVALID_PRODUCT_REFERENCE"
        assert saved.manifest_json["_lastNotification"]["httpStatus"] == 409
        assert saved.next_run_at is not None


@pytest.mark.asyncio
async def test_v2_isolated_historical_task_stops_retry_and_keeps_original_evidence(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env)

    def isolated(*_args, **_kwargs):
        raise worker.MultiControlHTTPError(409, "HISTORICAL_TASK_ISOLATED")

    monkeypatch.setattr(worker, "cut_multi_submit_request", isolated)
    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "FAILED"
        assert saved.error_code == "CUTFLOW_HISTORICAL_TASK_ISOLATED"
        assert saved.next_run_at is None
        assert saved.lease_token is None
        assert saved.failure_count == 0
        assert saved.output_json == []
        assert saved.manifest_path == f"inbox/multi-{task.id}/manifest.json"
        assert saved.manifest_json["manifestSha256"] == MANIFEST_HASH
        assert saved.manifest_json["_lastNotification"]["httpStatus"] == 409
        assert saved.manifest_json["_lastNotification"]["reason"] == "HISTORICAL_TASK_ISOLATED"
        assert "重新选择原片" in saved.error_message


def test_v2_http_error_body_only_exposes_whitelisted_reason():
    from io import BytesIO
    from urllib.error import HTTPError

    response = b'{"error":"IMPORT_REJECTED","reason":"INVALID_PRODUCT_REFERENCE","token":"private"}'
    error = HTTPError("http://cut.invalid", 409, "Conflict", {}, BytesIO(response))
    assert worker._http_detail(error) == "INVALID_PRODUCT_REFERENCE"
    error = HTTPError("http://cut.invalid", 409, "Conflict", {}, BytesIO(b'{"reason":"secret/token"}'))
    assert worker._http_detail(error) == ""


@pytest.mark.asyncio
async def test_stale_nas_revision_cannot_regress_newer_local_state(
    handoff_env, monkeypatch,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task = await create_v2_task(handoff_env, revision=3)
    async with database.session_factory() as session:
        stored = await session.get(MultiCutflowTask, task.id)
        stored.status = "EDITING"
        await session.commit()
        await session.refresh(stored)
        task = stored
    write_status(settings, task, status_payload(task.id, revision=2, status="failed"))
    imported = []
    monkeypatch.setattr(worker, "import_multi_outputs", lambda *args: imported.append(args))

    await worker.process_multi_task(database, settings, store, task, "lease-token")

    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task.id)
        assert saved.status == "EDITING"
        assert saved.error_code == "CUTFLOW_STALE_STATUS"
        assert saved.failure_count == 0
    assert imported == []


@pytest.mark.asyncio
async def test_classic_receipts_are_written_for_single_and_unified_tasks(handoff_env):
    database, settings, store, ids = handoff_env
    single_id = uuid.uuid4()
    multi_id = uuid.uuid4()
    output = {
        "id": "out-1",
        "sha256": "b" * 64,
        "size_bytes": 7,
        "duration_seconds": 1.0,
        "width": 180,
        "height": 320,
        "review_status": "PENDING",
        "created_at": datetime.now(UTC).isoformat(),
    }
    async with database.session_factory() as session:
        session.add(CutflowHandoff(
            id=single_id,
            user_id=ids["user"],
            parent_task_id=ids["task"],
            variant_id=ids["variant"],
            artifact_id=ids["artifact"],
            handoff_key=f"handoff-{single_id}",
            contract_version="libtv-cut-lan/1",
            status="PENDING_REVIEW",
            manifest_path=f"inbox/handoff-{single_id}/manifest.json",
            manifest_json={"manifestSha256": MANIFEST_HASH},
            cutflow_batch_id="batch-single",
            outputs_json=[output],
            failure_count=0,
        ))
        session.add(MultiCutflowTask(
            id=multi_id,
            user_id=ids["user"],
            idempotency_key=f"key-{multi_id}",
            payload_hash="d" * 64,
            status="PENDING_REVIEW",
            sources_json=[{"clipId": "clip-1", "order": 1}],
            manifest_json={
                "contractVersion": JOB_CONTRACT,
                "manifestSha256": MANIFEST_HASH,
                "_cutBatchId": "batch-multi",
            },
            manifest_path=f"inbox/multi-{multi_id}/manifest.json",
            output_json=[{**output, "task_id": str(multi_id)}],
            failure_count=0,
        ))
        await session.commit()

    assert await cutflow_receipts.ensure_receipts(database, settings) == 2
    single_receipt = json.loads(
        (settings.cutflow_handoff_root / "receipts" / "classic" / f"handoff-{single_id}.json")
        .read_text(encoding="utf-8")
    )
    multi_receipt = json.loads(
        (settings.cutflow_handoff_root / "receipts" / "classic" / f"multi-{multi_id}.json")
        .read_text(encoding="utf-8")
    )
    assert single_receipt["status"] == "PENDING_REVIEW"
    assert single_receipt["batchId"] == "batch-single"
    assert single_receipt["outputs"][0]["sha256"] == "b" * 64
    assert multi_receipt["batchId"] == "batch-multi"
    assert multi_receipt["sourceClipIds"] == ["clip-1"]
    assert await cutflow_receipts.ensure_receipts(database, settings) == 0
