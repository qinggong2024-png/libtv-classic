import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_worker_checkpoint as checkpoint  # noqa: E402


def fixture_db(tmp_path):
    path = tmp_path / "classic.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE cutflow_handoff(id TEXT,status TEXT,lease_token TEXT,lease_until TEXT);
            CREATE TABLE multi_cutflow_task(id TEXT,status TEXT,manifest_path TEXT,
                manifest_json TEXT,lease_token TEXT,lease_until TEXT);
        """)
        db.execute("INSERT INTO multi_cutflow_task VALUES (?,?,?,?,?,?)", (
            "original", "EDITING", "inbox/multi-original/manifest.json",
            json.dumps({"contractVersion": "libtv-cut-lan-multi/2",
                        "manifestSha256": "a" * 64}), "live-lease", "future",
        ))
    return path


def test_remote_editing_not_a_local_drain_blocker(tmp_path):
    path = fixture_db(tmp_path)
    initial = checkpoint.cutflow_checkpoint_evidence(path, drained=False)
    assert initial["activeRemoteTasks"] == initial["localLeases"] == 1
    with pytest.raises(RuntimeError, match="local/legacy"):
        checkpoint.cutflow_checkpoint_evidence(path, drained=True)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE multi_cutflow_task SET lease_token=NULL,lease_until=NULL")
    result = checkpoint.cutflow_checkpoint_evidence(path, drained=True)
    assert result["activeRemoteTasks"] == 1
    assert result["localLeases"] == 0
    checkpoint.verify_preserved_cutflow(path, initial["published"])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE multi_cutflow_task SET manifest_path='different'")
    with pytest.raises(RuntimeError, match="original CUT"):
        checkpoint.verify_preserved_cutflow(path, initial["published"])


@pytest.mark.parametrize("mode", ["legacy", "missing_hash", "ambiguous"])
def test_cutflow_handoff_fails_closed(tmp_path, mode):
    path = fixture_db(tmp_path)
    with sqlite3.connect(path) as db:
        if mode == "legacy":
            db.execute("INSERT INTO cutflow_handoff VALUES ('legacy','EDITING',NULL,NULL)")
        elif mode == "missing_hash":
            db.execute("UPDATE multi_cutflow_task SET manifest_json='{}'")
        else:
            db.execute("UPDATE multi_cutflow_task SET status='UNKNOWN'")
    with pytest.raises(RuntimeError):
        checkpoint.cutflow_checkpoint_evidence(path, drained=False)


@pytest.mark.parametrize("pair", [checkpoint.CUT_PAIR, checkpoint.CUT_NAS_PAIR, checkpoint.CUT_AUTO_SWEEP_TX_PAIR, checkpoint.CUT_NAS_RECONCILE_PAIR])
def test_online_handoff_installs_pin_only_after_real_drain(tmp_path, monkeypatch, pair):
    import classic_cutflow as cut
    import classic_green_api as green
    import classic_v2_canary_preflight as tasks
    import classic_worker_source as source

    path = fixture_db(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    old, new = sorted(pair)
    previous = tmp_path / "old"
    release = tmp_path / "new"
    previous.mkdir()
    release.mkdir()
    identity = {"sourceRevision": old, "token": "a" * 32}
    request = runtime / "checkpoint-cutflow-request.json"
    pin = runtime / "worker-cutflow-pin.json"
    pin.write_text(json.dumps({"sourceRevision": old}))
    monkeypatch.setattr(checkpoint.classic_lan, "STATE", runtime / "stack.json")
    monkeypatch.setattr(checkpoint.classic_lan, "ROOT", tmp_path)
    monkeypatch.setattr(checkpoint, "validated_release", lambda p, r: p)
    monkeypatch.setattr(checkpoint, "verify_worker_compatibility", lambda *a: None)
    monkeypatch.setattr(checkpoint, "supervisor_supports_blue", lambda *a: True)
    monkeypatch.setattr(checkpoint, "restart_held", lambda *a: False)
    monkeypatch.setattr(checkpoint, "owned_record", lambda *a: {})
    monkeypatch.setattr(checkpoint, "process_matches", lambda *a: True)
    monkeypatch.setattr(checkpoint.classic_lan, "owned_process",
                        lambda *a: None if request.exists() else True)
    monkeypatch.setattr(source, "worker_source", lambda *a: (previous, old))
    monkeypatch.setattr(green, "web_route", lambda: {"activeApiPort": 8002})
    monkeypatch.setattr(green, "json_get", lambda *a: {
        "configured": True, "newVideoAdmissionsPaused": False,
        "deploymentId": "libtv-classic-lan-0e3d1c8",
    })
    monkeypatch.setattr(cut, "cutflow_environment", lambda *a: {
        "CUTFLOW_TRANSPORT": "lan", "CUTFLOW_MULTI_SOURCE_ENABLED": "true",
        "CUTFLOW_NAS_STATUS_ENABLED": "true",
    })
    monkeypatch.setattr(tasks, "check", lambda *a, **kw: {
        "counts": {"uncertainPaidHistorical": 15},
    })

    def identity_status(*a):
        if not request.exists():
            return identity, "RUNNING"
        assert (runtime / "api-routing.lock").exists()
        assert json.loads(pin.read_text())["sourceRevision"] == old
        with sqlite3.connect(path) as db:
            db.execute("UPDATE multi_cutflow_task SET lease_token=NULL,lease_until=NULL")
        return identity, "DRAINED"

    monkeypatch.setattr(checkpoint, "read_identity", identity_status)
    assert checkpoint.online_cutflow_handoff(
        release, new, expected_old=old, baseline_unknown=15,
    )["action"] == "dry-run"
    assert not request.exists()
    result = checkpoint.online_cutflow_handoff(
        release, new, expected_old=old, baseline_unknown=15, apply=True,
    )
    assert result["preservedRemoteTasks"] == 1
    assert json.loads(pin.read_text())["sourceRevision"] == new
    assert not (runtime / "api-routing.lock").exists()
