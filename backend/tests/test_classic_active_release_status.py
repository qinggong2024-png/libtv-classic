"""Offline tests for the read-only Classic blue/green source-of-truth report."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "classic_active_release_status.py"
    spec = importlib.util.spec_from_file_location("classic_active_release_status", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_green_reports_runtime_source_and_admin_unsynced(tmp_path, monkeypatch):
    mod = _load_module()
    root = tmp_path / "classic-main"
    green = tmp_path / "release"
    root.mkdir()
    green.mkdir()
    for name in mod.GALLERY_FILES:
        file = green / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("deployed", encoding="utf8")
    active_status = {"sourceRevision": "active-code-64", "configRevision": "same", "schemaRevision": "same"}
    blue_status = {"sourceRevision": "old-code-64", "configRevision": "same", "schemaRevision": "same"}
    responses = {
        mod.URL + "/__classic_api_route/status": {"activeApiPort": 8003},
        mod.URL + "/api/v1/system/classic-new-video-admission": {"newVideoAdmissionsPaused": False},
        mod.URL + "/api/v1/health/ready": {"status": "ready"},
        "http://127.0.0.1:8003/api/v1/system/status": active_status,
        "http://127.0.0.1:8002/api/v1/system/status": blue_status,
    }
    monkeypatch.setattr(mod, "get_json", responses.get)
    monkeypatch.setattr(mod, "read_json_file", lambda _: {
        "releaseRoot": str(green), "sourceRevision": "active-code-64",
    })
    monkeypatch.setattr(mod, "git_head", lambda p: "green" if p == green else "main")
    outcome = mod.report(root)
    assert outcome["ok"] is True
    assert outcome["activeApiPort"] == 8003
    assert outcome["activeApi"]["root"] == str(green)
    assert outcome["activeApi"]["gitHead"] == "green"
    assert outcome["mainCheckout"]["gitHead"] == "main"
    assert outcome["greenManifestMatchesRunningApi"] is True
    assert outcome["admissionPaused"] is False
    assert outcome["galleryFilesDifferentFromMain"] == list(mod.GALLERY_FILES)
    assert outcome["notes"]


def test_active_green_mismatched_fingerprint_is_not_healthy(tmp_path, monkeypatch):
    mod = _load_module()
    root = tmp_path / "main"
    root.mkdir()
    monkeypatch.setattr(mod, "get_json", lambda url: {
        mod.URL + "/__classic_api_route/status": {"activeApiPort": 8003},
        "http://127.0.0.1:8003/api/v1/system/status": {"sourceRevision": "different"},
    }.get(url))
    monkeypatch.setattr(mod, "read_json_file", lambda _: {
        "releaseRoot": str(root), "sourceRevision": "expected",
    })
    outcome = mod.report(root)
    assert outcome["ok"] is False
    assert outcome["greenManifestMatchesRunningApi"] is False
    assert any("fingerprint" in n for n in outcome["notes"])


def test_no_route_fails_closed(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "get_json", lambda _: None)
    assert mod.report()["ok"] is False


def test_frontend_ready_only_for_verified_sqlite_write_contention(tmp_path, monkeypatch):
    mod = _load_module()
    rev = "a" * 64
    status = {"sourceRevision": rev, "configRevision": "config", "schemaRevision": "schema"}
    route = {"activeApiPort": 8003, "configured": True, "hotRouteVersion": 1, "allowedApiPorts": [8002, 8003]}
    gate = {"configured": True, "deploymentId": "libtv-classic-lan-0e3d1c8", "newVideoAdmissionsPaused": False}
    responses = {mod.URL + "/__classic_api_route/status": route,
                 mod.URL + "/api/v1/system/classic-new-video-admission": gate,
                 mod.URL + "/api/v1/system/status": status,
                 "http://127.0.0.1:8003/api/v1/system/status": status,
                 mod.URL + "/api/v1/health/live": {"status": "ok"}}
    monkeypatch.setattr(mod, "get_json", responses.get)
    monkeypatch.setattr(mod, "read_json_file", lambda _: {"releaseRoot": str(tmp_path), "sourceRevision": rev})
    monkeypatch.setattr(mod, "workspace_source_revision", lambda _: rev)
    monkeypatch.setattr(mod, "sqlite_write_only_unavailable", lambda _: True)
    monkeypatch.setattr(mod, "sqlite_contention_or_recovered", lambda _: True)
    result = mod.report(tmp_path)
    assert not result["ok"] and result["frontendReady"]
    assert result["frontendWarnings"] == ["SQLITE_WRITE_PROBE_UNAVAILABLE_READ_PATH_VERIFIED"]
    assert mod.verify_release(result, expected_api_revision=rev, expected_frontend_sha256=None, expected_main_head=None)
    responses[mod.URL + "/api/v1/system/status"] = dict(status, sourceRevision="b" * 64)
    assert not mod.report(tmp_path)["frontendReady"]
    responses[mod.URL + "/api/v1/system/status"] = status
    monkeypatch.setattr(mod, "sqlite_contention_or_recovered", lambda _: False)
    assert not mod.report(tmp_path)["frontendReady"]
    monkeypatch.setattr(mod, "sqlite_contention_or_recovered", lambda _: True)
    monkeypatch.setattr(mod, "sqlite_write_only_unavailable", lambda _: False)
    assert not mod.report(tmp_path)["frontendReady"]


def test_lock_probe_accepts_busy_without_writing_rows(tmp_path):
    import sqlite3
    mod = _load_module()
    path = tmp_path / "var/classic-lan/classic.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE parent_task (id TEXT)")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        assert mod.sqlite_contention_or_recovered(tmp_path)
        db.rollback()
        assert db.execute("SELECT count(*) FROM parent_task").fetchone() == (0,)
    db.close()
    assert mod.sqlite_contention_or_recovered(tmp_path)
    assert not mod.sqlite_contention_or_recovered(tmp_path / "missing")


def test_exact_503_body_only(monkeypatch):
    import io
    from urllib.error import HTTPError
    mod = _load_module()
    class Opener:
        def __init__(self, code, body):
            self.code, self.body = code, body
        def open(self, url, timeout):
            raise HTTPError(url, self.code, "test", {}, io.BytesIO(self.body))
    for code, body, expected in [
        (503, b'{"status":"degraded","checks":{"sqliteWrite":"unavailable"}}', True),
        (502, b'{"status":"degraded","checks":{"sqliteWrite":"unavailable"}}', False),
        (503, b'{"status":"degraded","checks":{"postgresql":"unavailable"}}', False),
        (503, b'not json', False),
    ]:
        monkeypatch.setattr(mod, "build_opener", lambda _: Opener(code, body))
        assert mod.sqlite_write_only_unavailable("http://example.invalid") is expected
