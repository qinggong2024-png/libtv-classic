"""Hybrid recovery uses temporary DB/media/share only; no live CUT or providers."""

import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime
from urllib.error import URLError

import pytest
import test_cutflow_lan as lan
from libtv_agent.domain.models import CutflowHandoff
from libtv_agent.runner import cutflow_worker
from libtv_agent.services.cutflow_handoff import (
    read_nas_status,
    sha256_file,
    write_json_atomic,
)
from libtv_agent.services.cutflow_policy import legacy_lan_policy
from sqlalchemy import func, select

base_handoff_env = lan.base_handoff_env
product_handoff_env = lan.product_handoff_env
handoff_env = lan.handoff_env


def status_path(settings, row):
    return settings.cutflow_handoff_root / "outbox" / row.handoff_key / "status.json"


def receipt(row, **changes):
    return {
        "handoffId": row.handoff_key, "manifestSha256": row.manifest_json["manifestSha256"],
        "editPolicyReceipt": lan.policy_receipt(row.manifest_json),
        "status": "editing", "outputs": [], **changes,
    }


async def offline_publish(env, monkeypatch):
    database, settings, store, _ids = env
    settings.cutflow_nas_status_enabled = True
    row = await lan.shared.enqueue(env)

    def offline(*_args):
        raise URLError("private network detail")

    monkeypatch.setattr(cutflow_worker, "control_request", offline)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        return await session.get(CutflowHandoff, row.id)


async def test_lost_notifications_stay_recoverable_and_scanner_receipt_returns_real_media(
    handoff_env, monkeypatch, tmp_path,
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("media validation requires ffmpeg and ffprobe")
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    original = (package / "manifest.json").read_bytes()
    posts = []

    def lost(_settings, method, _suffix, payload=None):
        assert method == "POST"
        posts.append(dict(payload))
        raise TimeoutError("response lost")

    monkeypatch.setattr(cutflow_worker, "control_request", lost)
    for _ in range(6):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "READY" and saved.failure_count == 7
        assert 0 < (saved.next_run_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() <= 60
        assert saved.manifest_json == row.manifest_json
    assert all(item == row.manifest_json for item in posts)
    assert (package / "manifest.json").read_bytes() == original
    assert json.loads((package / "READY").read_bytes())["sha256"] == sha256_file(
        package / "manifest.json",
    )

    video = status_path(settings, row).with_name("render-1.mp4")
    video.parent.mkdir(parents=True)
    await asyncio.to_thread(subprocess.run, [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x288:rate=10",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    write_json_atomic(status_path(settings, row), receipt(row, status="completed", outputs=[{
        "id": "render-1", "relative_path": video.name, "sha256": sha256_file(video),
        "size_bytes": video.stat().st_size,
    }]))

    def forbidden(*_args):
        pytest.fail("scanner already accepted the package; must not POST again")

    monkeypatch.setattr(cutflow_worker, "control_request", forbidden)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW" and saved.failure_count == 0
        assert len(saved.outputs_json) == 1
        output = saved.outputs_json[0]
        assert output["review_status"] == "PENDING"
        local = await store.localize(output["object_key"], tmp_path / "readback.mp4")
        assert local.read_bytes() == video.read_bytes()
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1
    await lan.due(database, row)
    assert not await cutflow_worker.tick(database, settings, store)


@pytest.mark.parametrize("channel", ["http", "nas"])
@pytest.mark.parametrize("damage", ["missing", "same-size-corrupt", "truncated"])
async def test_cached_completed_cannot_import_damaged_media_and_recovers_same_output(
    handoff_env, monkeypatch, tmp_path, channel, damage,
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("media validation requires ffmpeg and ffprobe")
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    write_json_atomic(status_path(settings, row), receipt(row))
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)

    video = status_path(settings, row).with_name("render-1.mp4")
    await asyncio.to_thread(subprocess.run, [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x288:rate=10",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    original = video.read_bytes()
    completed = receipt(row, status="completed", outputs=[{
        "id": "render-1", "relative_path": video.name, "sha256": sha256_file(video),
        "size_bytes": len(original),
    }])
    write_json_atomic(status_path(settings, row), completed)
    snapshot = status_path(settings, row).read_bytes()
    # The receipt was valid when published, but its media changed before import.
    if damage == "missing":
        video.unlink()
    elif damage == "same-size-corrupt":
        corrupted = bytearray(original)
        corrupted[len(corrupted) // 2] ^= 1
        video.write_bytes(corrupted)
    else:
        video.write_bytes(original[:len(original) // 2])
    calls = []

    def cached(_settings, method, _suffix, payload=None):
        calls.append(method)
        assert method == "GET", "accepted output must never create another edit"
        if channel == "nas":
            raise URLError("HTTP unavailable")
        return completed

    monkeypatch.setattr(cutflow_worker, "control_request", cached)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.outputs_json == []
        assert saved.error_code == (
            "CUTFLOW_MEDIA_UNAVAILABLE" if damage == "missing" else "VIDEO_HASH_MISMATCH"
        )
        assert saved.next_run_at is not None
    assert status_path(settings, row).read_bytes() == snapshot

    # Restoring the original immutable bytes recovers without POST or a new id.
    video.write_bytes(original)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW" and saved.error_code is None
        assert len(saved.outputs_json) == 1
        output = saved.outputs_json[0]
        assert output["id"] == "render-1" and output["review_status"] == "PENDING"
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1
    assert calls == ["GET", "GET"]

    # Once imported, the verified local copy remains valid if NAS later breaks.
    video.write_bytes(b"corrupted after successful import")
    await lan.due(database, row)
    assert not await cutflow_worker.tick(database, settings, store)
    local = await store.localize(output["object_key"], tmp_path / "readback.mp4")
    assert local.read_bytes() == original


@pytest.mark.parametrize("failure", ["network", 404, 408, 429, 503])
async def test_post_response_loss_recovers_same_cycle_then_get_outage_uses_same_receipt(
    handoff_env, monkeypatch, failure,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_nas_status_enabled = True
    row = await lan.shared.enqueue(handoff_env)
    calls = []

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        if method == "POST":
            # CUT's scanner and HTTP receiver share the same persisted acceptance.
            row.manifest_json = payload
            write_json_atomic(status_path(settings, row), receipt(row))
        if failure == "network":
            raise URLError("unavailable")
        raise cutflow_worker.ControlHTTPError(failure)

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    for _ in range(3):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    assert calls == ["POST", "GET", "GET"]
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None
        assert saved.manifest_json["_acceptedManifestSha256"] == row.manifest_json["manifestSha256"]


@pytest.mark.parametrize("channel", ["http", "nas"])
@pytest.mark.parametrize("delivery_code", ["NAS_UNAVAILABLE", "HASH_MISMATCH", "SIZE_MISMATCH"])
async def test_cut_delivery_failure_recovers_same_receipt_after_repeated_faults(
    handoff_env, monkeypatch, tmp_path, channel, delivery_code,
):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("media validation requires ffmpeg and ffprobe")
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    write_json_atomic(status_path(settings, row), receipt(row))
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    response = receipt(row, status="failed", error={
        "code": delivery_code, "message": "private remote path and token: never display",
    })
    write_json_atomic(status_path(settings, row), response)
    calls = []

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        assert method == "GET", "a delivery fault must never submit another edit"
        if channel == "nas":
            raise URLError("HTTP unavailable")
        return response

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    for _ in range(6):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.failure_count == 6
        assert saved.outputs_json == [] and saved.error_code == "CUTFLOW_MEDIA_UNAVAILABLE"
        assert "private" not in saved.error_message
        assert 0 < (saved.next_run_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() <= 60
        assert saved.manifest_json["_acceptedManifestSha256"] == row.manifest_json["manifestSha256"]

    video = status_path(settings, row).with_name("restored.mp4")
    await asyncio.to_thread(subprocess.run, [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x288:rate=10",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    response = receipt(row, status="completed", outputs=[{
        "id": "render-1", "relative_path": video.name, "sha256": sha256_file(video),
        "size_bytes": video.stat().st_size,
    }])
    write_json_atomic(status_path(settings, row), response)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW" and saved.failure_count == 0
        assert saved.error_code is None and len(saved.outputs_json) == 1
        output = saved.outputs_json[0]
        assert output["id"] == "render-1" and output["sha256"] == sha256_file(video)
        local = await store.localize(output["object_key"], tmp_path / "recovered.mp4")
        assert local.read_bytes() == video.read_bytes()
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 1
    assert calls == ["GET"] * 7


@pytest.mark.parametrize("mode", ["disabled", "legacy-lan"])
async def test_delivery_failure_retry_does_not_change_other_lan_modes(
    handoff_env, monkeypatch, mode,
):
    database, settings, store, _ids = handoff_env
    settings.cutflow_nas_status_enabled = mode != "disabled"
    row = await lan.shared.enqueue(handoff_env)
    if mode == "legacy-lan":
        async with database.session_factory() as session:
            saved = await session.get(CutflowHandoff, row.id)
            saved.manifest_json = {**saved.manifest_json, "edit_policy": legacy_lan_policy()}
            await session.commit()
    calls, descriptor = [], {}

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        if payload:
            descriptor.update(payload)
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "editPolicyReceipt": lan.policy_receipt(descriptor), "outputs": [],
                "status": "editing" if method == "POST" else "failed",
                "error": {"code": "NAS_UNAVAILABLE"}}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "FAILED" and saved.next_run_at is None
        assert saved.outputs_json == [] and saved.error_code == "CUTFLOW_EDIT_FAILED"
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("defect", [
    "handoff", "hash", "policy", "receipt-hash", "missing-policy", "partial", "empty",
    "malformed", "list", "oversize", "missing-media", "failed",
])
async def test_nas_receipt_cannot_bypass_identity_policy_or_media(handoff_env, monkeypatch, defect):
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    body = receipt(row, status="completed", outputs=[{
        "id": "render-1", "relative_path": "missing.mp4", "sha256": "a" * 64, "size_bytes": 1,
    }])
    expected = "INVALID_RESULT"
    if defect == "handoff":
        body["handoffId"] = "other-handoff"
    elif defect == "hash":
        body["manifestSha256"] = "b" * 64
    elif defect in {"policy", "receipt-hash", "missing-policy"}:
        expected = "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED"
        if defect == "missing-policy":
            body.pop("editPolicyReceipt")
        elif defect == "policy":
            body["editPolicyReceipt"]["policyVersion"] = "yiying-ootd/1"
        else:
            body["editPolicyReceipt"]["manifestSha256"] = "b" * 64
    elif defect == "partial":
        body["status"] = "editing"
    elif defect == "empty":
        body["outputs"] = []
    elif defect == "missing-media":
        expected = "CUTFLOW_MEDIA_UNAVAILABLE"
    elif defect == "failed":
        body.update(status="failed", outputs=[], error={"code": "QUALITY_REJECTED"})
        expected = "CUTFLOW_QUALITY_REJECTED"
    path = status_path(settings, row)
    write_json_atomic(path, body)
    if defect in {"malformed", "list", "oversize"}:
        path.write_bytes({"malformed": b"{", "list": b"[]", "oversize": b" " * 1_048_577}[defect])
    for _ in range(6 if defect == "missing-media" else 1):
        await lan.due(database, row)
        await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.error_code == expected and saved.outputs_json == []
        assert saved.status != "PENDING_REVIEW"
        if defect == "missing-media":
            assert saved.status == "EDITING" and saved.next_run_at is not None
        if defect == "failed":
            assert saved.status == "FAILED" and saved.next_run_at is None


@pytest.mark.parametrize("mode", ["disabled", "legacy-lan", "bridge"])
async def test_fallback_never_reads_old_or_disabled_queues(handoff_env, monkeypatch, mode):
    _database, settings, _store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    path = status_path(settings, row)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"invalid: must never read")
    if mode == "disabled":
        settings.cutflow_nas_status_enabled = False
    elif mode == "legacy-lan":
        row.manifest_json = {**row.manifest_json, "editPolicy": legacy_lan_policy()}
    else:
        settings.cutflow_transport = "bridge"
    assert read_nas_status(row, settings) is None


async def test_enabled_hybrid_keeps_legacy_lan_http_contract(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    settings.cutflow_nas_status_enabled = True
    row = await lan.shared.enqueue(handoff_env)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        saved.manifest_json = {**saved.manifest_json, "edit_policy": legacy_lan_policy()}
        await session.commit()
    path = status_path(settings, row)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old snapshot: do not consume")
    calls, descriptor = [], {}

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        if payload:
            descriptor.update(payload)
        # Legacy LAN does not require the /2 policy receipt.
        return {"handoffId": row.handoff_key, "manifestSha256": descriptor["manifestSha256"],
                "status": "editing", "outputs": []}

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    assert calls == ["POST", "GET"]
    assert descriptor["editPolicy"] == legacy_lan_policy()
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None


async def test_unreadable_nas_status_does_not_block_http(handoff_env, monkeypatch):
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    # Opening a directory as a file fails on both Windows and POSIX.
    status_path(settings, row).mkdir(parents=True)
    calls = []

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        return receipt(row)

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    assert calls == ["POST", "GET"]
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING" and saved.error_code is None


@pytest.mark.parametrize("reply", ["healthy", "auth", "invalid-policy"])
async def test_http_has_priority_and_auth_or_protocol_errors_are_not_hidden_by_nas(
    handoff_env, monkeypatch, reply,
):
    database, settings, store, _ids = handoff_env
    row = await offline_publish(handoff_env, monkeypatch)
    write_json_atomic(status_path(settings, row), receipt(row))
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    write_json_atomic(status_path(settings, row), receipt(row, status="failed"))
    calls = []

    def remote(_settings, method, _suffix, payload=None):
        calls.append(method)
        if reply == "auth":
            raise cutflow_worker.ControlHTTPError(401)
        return receipt(row, **({"editPolicyReceipt": None} if reply == "invalid-policy" else {}))

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await lan.due(database, row)
    await cutflow_worker.tick(database, settings, store)
    assert calls == ["GET"]
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "EDITING"
        assert saved.error_code == {
            "healthy": None, "auth": "CUTFLOW_HTTP_ERROR",
            "invalid-policy": "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED",
        }[reply]
