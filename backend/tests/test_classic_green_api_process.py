"""No-provider tests for pinned, fail-closed green API supervision."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_green_api as manager  # noqa: E402


def test_auto_restart_reuses_pinned_source_and_never_moves_proxy_route(monkeypatch):
    release = ROOT
    expected = "a" * 64
    manifest = {
        "version": 1,
        "pid": 101,
        "created": 1234.0,
        "releaseRoot": str(release),
        "sourceRevision": expected,
        "lastStartUtc": (datetime.now(UTC) - timedelta(minutes=3)).isoformat(),
        "restartCount": 1,
    }
    launched = []
    monkeypatch.setattr(manager, "read_manifest", lambda: manifest)
    monkeypatch.setattr(manager, "green_process", lambda _manifest: None)
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8003})
    monkeypatch.setattr(
        manager,
        "start_green",
        lambda root, fingerprint, **kwargs: launched.append((root, fingerprint, kwargs)),
    )
    assert manager.maybe_recover_green()
    assert launched == [(release, expected, {"recovering": True})]
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8002})
    assert manager.maybe_recover_green() is False
    assert len(launched) == 1


def test_dead_active_green_respects_restart_budget_and_cooldown(monkeypatch):
    manifest = {
        "version": 1,
        "pid": 101,
        "created": 1234.0,
        "releaseRoot": str(ROOT),
        "sourceRevision": "a" * 64,
        "lastStartUtc": datetime.now(UTC).isoformat(),
        "restartCount": 1,
    }
    monkeypatch.setattr(manager, "read_manifest", lambda: manifest)
    monkeypatch.setattr(manager, "green_process", lambda _manifest: None)
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8003})
    assert manager.maybe_recover_green() is False
    manifest["lastStartUtc"] = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    manifest["restartCount"] = 3
    with pytest.raises(RuntimeError, match="retry budget"):
        manager.maybe_recover_green()


def test_no_standby_worker_allowed_and_identity_checked(monkeypatch, tmp_path):
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(manager, "STORAGE", tmp_path)
    expected = "e" * 64
    source = tmp_path / "approved-release"
    source.mkdir()
    classic_source = tmp_path / "classic-source"
    classic_source.mkdir()
    monkeypatch.setattr(manager, "ROOT", classic_source)
    # Explicit source identity must always be verified before a new process.
    monkeypatch.setattr(manager, "source_fingerprint", lambda _release: "f" * 64)
    with pytest.raises(RuntimeError, match="source files changed"):
        manager.validated_release(source, expected)

    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8003})
    with pytest.raises(RuntimeError, match="second green"):
        manager.start_green(source, expected)


def test_green_can_only_be_stopped_after_routing_back_and_draining(monkeypatch, tmp_path):
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(manager, "STORAGE", tmp_path)
    manifest = {
        "version": 1,
        "pid": 101,
        "created": 1234.0,
        "releaseRoot": str(tmp_path),
        "sourceRevision": "a" * 64,
    }
    monkeypatch.setattr(manager, "read_manifest", lambda: manifest)
    monkeypatch.setattr(
        manager,
        "web_route",
        lambda: {
            "activeApiPort": 8003,
            "inFlightByPort": {"8003": 0},
        },
    )
    with pytest.raises(RuntimeError, match="currently serving"):
        manager.stop_green()
    monkeypatch.setattr(
        manager,
        "web_route",
        lambda: {
            "activeApiPort": 8002,
            "inFlightByPort": {"8003": 1},
        },
    )
    with pytest.raises(RuntimeError, match="in-flight"):
        manager.stop_green()


def test_supervised_boot_can_wait_for_pinned_green_only_when_explicitly_enabled(monkeypatch):
    import classic_lan

    old_record = {"version": 1, "pid": 123, "created": 0}
    monkeypatch.setattr(manager, "read_manifest", lambda: old_record)
    monkeypatch.setattr(manager, "green_process", lambda _record: None)
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8003})
    monkeypatch.delenv("CLASSIC_MANAGED_GREEN_RECOVERY", raising=False)
    assert classic_lan.supervised_green_recovery_pending() is False
    monkeypatch.setenv("CLASSIC_MANAGED_GREEN_RECOVERY", "1")
    assert classic_lan.supervised_green_recovery_pending() is True
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8002})
    assert classic_lan.supervised_green_recovery_pending() is False
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8003})
    monkeypatch.setattr(manager, "green_process", lambda _record: object())
    assert classic_lan.supervised_green_recovery_pending() is False


def test_standby_start_launches_only_loopback_api_using_original_classic_context(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(manager, "STORAGE", tmp_path)
    approved = tmp_path / "approved-version"
    approved.mkdir()
    fingerprint = "e" * 64
    monkeypatch.setattr(manager, "validated_release", lambda *_args: approved)
    monkeypatch.setattr(manager, "read_manifest", lambda: None)
    monkeypatch.setattr(manager, "web_route", lambda: {"activeApiPort": 8002})

    def fake_http(port, path):
        assert port in {8002, 8003}
        if path.endswith("classic-new-video-admission"):
            return {
                "deploymentId": manager.DEPLOYMENT,
                "configured": True,
                "newVideoAdmissionsPaused": True,
            }
        if path.endswith("system/status"):
            return {"sourceRevision": fingerprint}
        return {"ready": True}

    monkeypatch.setattr(manager, "json_get", fake_http)
    monkeypatch.setattr(
        manager,
        "lan_environment",
        lambda *_: {
            "DATABASE_URL": "sqlite+aiosqlite:///synthetic-test.db",
            "SESSION_SECRET": "test-session-only",
            "PYTHONPATH": "old-code",
        },
    )

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def connect_ex(self, _address):
            return 1

    monkeypatch.setattr(manager.socket, "socket", Socket)
    started = []

    class FakeSubprocess:
        pid = 202020

        def poll(self):
            return None

    def popen(args, **kwargs):
        started.append((args, kwargs))
        return FakeSubprocess()

    monkeypatch.setattr(manager.subprocess, "Popen", popen)
    monkeypatch.setattr(
        manager.psutil,
        "Process",
        lambda _pid: type("FakeProcess", (), {"create_time": lambda self: 12345.0})(),
    )
    saved = []
    monkeypatch.setattr(manager, "write_manifest", saved.append)
    record = manager.start_green(approved, fingerprint)
    assert record["sourceRevision"] == fingerprint
    assert len(started) == 1
    command, opts = started[0]
    assert "uvicorn" in command
    assert "--port" in command and "8003" in command
    assert not any("classic_runner" in part or "classic_cutflow" in part for part in command)
    assert opts["env"]["APP_PORT"] == "8003"
    assert opts["env"]["SESSION_SECRET"] == "test-session-only"
    assert opts["env"]["DATABASE_URL"] == "sqlite+aiosqlite:///synthetic-test.db"
    assert opts["env"]["PYTHONPATH"] == str(approved / "backend" / "src")
    assert saved == [record]


def test_green_manifest_rejects_bad_structure(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "MANIFEST", tmp_path / "green.json")
    assert manager.read_manifest() is None
    manager.MANIFEST.write_text(json.dumps({"version": 3, "pid": "wrong"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid green"):
        manager.read_manifest()


def test_upgraded_proxy_requires_process_local_drain_before_green_stop(monkeypatch, tmp_path):
    import classic_api_drain

    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(manager, "STORAGE", tmp_path)
    monkeypatch.setattr(manager, "read_manifest", lambda: {"sourceRevision": "a" * 64})
    monkeypatch.setattr(manager, "web_route", lambda: {
        "activeApiPort": 8002, "inFlightByPort": {"8003": 0},
        "drainContractVersion": 1,
    })
    monkeypatch.setattr(classic_api_drain, "inspect", lambda *_: {"ready": False})
    monkeypatch.setattr(manager, "green_process", lambda *_: pytest.fail("would stop API"))
    with pytest.raises(RuntimeError, match="local work"):
        manager.stop_green()
    assert not (tmp_path / "runtime" / "api-routing.lock").exists()
