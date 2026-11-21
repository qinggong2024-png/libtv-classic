"""The diagnostic CLI only reads one job; network access is GET-only."""

import argparse
import importlib.util
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("cutflow_doctor_under_test",
                                              SCRIPTS / "classic_cutflow_doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


def fixture_storage(tmp_path):
    storage = tmp_path / "storage"
    runtime = storage / "runtime"
    runtime.mkdir(parents=True)
    nas = tmp_path / "nas"
    key = f"handoff-{uuid.uuid4()}"
    package = nas / "inbox" / key
    package.mkdir(parents=True)
    (package / "clip.mp4").write_bytes(b"test-source")
    media_hash = doctor.digest_file(package / "clip.mp4")
    (package / "manifest.json").write_text(json.dumps({
        "handoff_id": key, "assets": [{"relative_path": "clip.mp4", "size_bytes": 11,
                                      "sha256": media_hash}],
    }))
    digest = doctor.digest_file(package / "manifest.json")
    (package / "READY").write_text(json.dumps({"sha256": digest}))
    (runtime / "cutflow-config.json").write_text(json.dumps({
        "version": 1, "enabled": True, "transport": "lan", "baseUrl": "http://cut.test:3001",
        "handoffRoot": str(nas),
    }))
    with sqlite3.connect(storage / "classic.db") as database:
        database.execute("CREATE TABLE cutflow_handoff (id TEXT, handoff_key TEXT, status TEXT,"
                         "cutflow_batch_id TEXT,error_code TEXT,failure_count INTEGER,"
                         "next_run_at TEXT,lease_until TEXT,manifest_json TEXT)")
        database.execute("INSERT INTO cutflow_handoff VALUES(?,?,?,?,?,?,?,?,?)", (
            key[8:].replace("-", ""), key, "IMPORTING", None, None, 0, None, None,
            json.dumps({"manifestSha256": digest}),
        ))
    return storage, nas, key, digest


def test_readonly_doctor_checks_exact_target_and_does_not_echo_remote_data(tmp_path, monkeypatch):
    storage, _nas, key, digest = fixture_storage(tmp_path)
    before = (storage / "classic.db").read_bytes()
    calls = []

    def remote(_settings, method, suffix):
        calls.append((method, suffix))
        return {"handoffId": key, "manifestSha256": digest, "status": "importing",
                "outputs": [], "private": "should-never-be-exported"}

    monkeypatch.setattr(doctor, "control_request", remote)
    result = doctor.diagnose(storage, key, verify_media=True)
    assert result["nas"] == {"manifest": True, "ready": True, "mediaVerified": True}
    assert result["diagnosticGap"] == "CUT_RUNTIME_DIAGNOSTICS_NOT_AVAILABLE"
    assert calls == [("GET", f"/{key}")]
    assert "should-never" not in json.dumps(result)
    assert (storage / "classic.db").read_bytes() == before


def test_identity_mismatch_and_unsafe_nas_paths_do_not_read_outside_package(tmp_path, monkeypatch):
    storage, nas, key, digest = fixture_storage(tmp_path)
    monkeypatch.setattr(doctor, "control_request", lambda *_args: {
        "handoffId": "wrong-job", "manifestSha256": digest,
    })
    result = doctor.diagnose(storage, key)
    assert result["blockerCode"] == "REMOTE_IDENTITY_MISMATCH"
    assert "remote" not in result
    manifest = nas / "inbox" / key / "manifest.json"
    manifest.write_text(json.dumps({"handoff_id": key, "assets": [{
        "relative_path": "../../outside", "size_bytes": 0, "sha256": "b" * 64,
    }]}))
    changed = doctor.digest_file(manifest)
    (manifest.parent / "READY").write_text(json.dumps({"sha256": changed}))
    assert doctor.package_check(nas, key, changed, True)["errorCode"]


def test_job_doctor_reads_cut_status_and_classic_receipt(tmp_path):
    storage = tmp_path / "storage"
    runtime = storage / "runtime"
    runtime.mkdir(parents=True)
    nas = tmp_path / "nas"
    task_id = str(uuid.uuid4())
    manifest_hash = "a" * 64
    (runtime / "cutflow-config.json").write_text(json.dumps({
        "version": 1, "enabled": True, "transport": "lan",
        "baseUrl": "http://cut.test:3001", "handoffRoot": str(nas),
        "nasStatusEnabled": True,
    }))
    output = nas / "outbox" / task_id / "out.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"verified-output")
    output_hash = doctor.digest_file(output)
    (output.parent / "status.json").write_text(json.dumps({
        "schemaVersion": "yiying-cut-status/2",
        "revision": 2,
        "taskId": task_id,
        "handoffKey": f"multi-{task_id}",
        "manifestSha256": manifest_hash,
        "batchId": "batch-1",
        "status": "completed",
        "outputs": [{
            "outputId": "out-1",
            "relativePath": f"outbox/{task_id}/out.mp4",
            "sha256": output_hash,
            "size": len(b"verified-output"),
        }],
    }))
    receipt = nas / "receipts" / "classic" / f"multi-{task_id}.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "schemaVersion": "yiying-classic-import/1",
        "writer": "classic",
        "status": "PENDING_REVIEW",
        "handoffKey": f"multi-{task_id}",
        "manifestSha256": manifest_hash,
        "outputs": [{
            "id": "out-1", "sha256": output_hash,
            "sizeBytes": len(b"verified-output"),
        }],
    }))
    with sqlite3.connect(storage / "classic.db") as database:
        database.execute(
            "CREATE TABLE multi_cutflow_task (id TEXT,status TEXT,manifest_json TEXT,"
            "output_json TEXT,error_code TEXT,error_message TEXT,failure_count INTEGER,"
            "next_run_at TEXT,lease_until TEXT,updated_at TEXT)"
        )
        database.execute(
            "INSERT INTO multi_cutflow_task VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                task_id.replace("-", ""), "PENDING_REVIEW",
                json.dumps({"contractVersion": "libtv-cut-lan-multi/2",
                            "manifestSha256": manifest_hash}),
                json.dumps([{"id": "out-1", "sha256": output_hash,
                             "size_bytes": len(b"verified-output")}]),
                None, None, 0, None, None, "2026-10-29 10:24:04",
            ),
        )
    result = doctor.diagnose_job(storage, task_id, verify_media=True)
    assert result["local"]["status"] == "PENDING_REVIEW"
    assert result["cutStatus"]["status"] == "completed"
    assert result["cutStatus"]["revision"] == 2
    assert result["cutStatus"]["mediaVerified"] is True
    assert result["classicReceipt"]["valid"] is True


@pytest.mark.parametrize("key", ["../../secret", "handoff-not-a-uuid", "x", "handoff-$(whoami)"])
def test_cli_requires_canonical_job_id(key):
    with pytest.raises(argparse.ArgumentTypeError):
        doctor.handoff_key(key)
