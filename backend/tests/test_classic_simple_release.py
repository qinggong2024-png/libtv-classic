"""The simplified release entry must fail closed before any live write."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("classic_release", ROOT / "scripts" / "classic_release.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fake_live(head: str, paused: bool = False) -> dict:
    return {
        "ok": True,
        "activeApiPort": 8003,
        "mainCheckout": {"gitHead": head, "matchesRunning": True},
        "activeApi": {"sourceRevision": "a" * 64, "diskMatchesRunning": True},
        "admissionPaused": paused,
        "activeConfigRevision": "config-test",
        "activeSchemaRevision": "schema-test",
        "frontendIndexSha256": "b" * 64,
    }


def setup_check(monkeypatch, *, paused=False, cut_busy=False):
    head = "c" * 40
    monkeypatch.setattr(MODULE, "report", lambda root: fake_live(head, paused))
    monkeypatch.setattr(MODULE, "git", lambda root, *args: {
        ("rev-parse", "HEAD"): head,
        ("branch", "--show-current"): "main",
        ("status", "--porcelain"): "",
    }[args])
    monkeypatch.setattr(
        MODULE, "check_tasks",
        lambda *args, **kwargs: {
            "blockers": ["CUT_JOBS_IN_FLIGHT"] if cut_busy else [],
            "counts": {"activeCutMulti": 1 if cut_busy else 0},
        },
    )
    monkeypatch.setattr(MODULE, "committed_changes", lambda root, base: {"frontendHotEligible": True, "target": head, "mode": "frontend-hot"})
    return head


def test_check_accepts_clean_matching_live_main(monkeypatch, tmp_path):
    head = setup_check(monkeypatch)
    result = MODULE.assess(tmp_path, head, "a" * 64)
    assert result["ready"]
    assert result["mainHead"] == head


def test_publish_requires_paused_intake_and_correct_pinned_revision(monkeypatch, tmp_path):
    head = setup_check(monkeypatch)
    result = MODULE.publish_frontend(tmp_path, head, "a" * 64, 15)
    assert not result["ready"]
    assert "NEW_TASK_INTAKE_NOT_PAUSED" in result["blockers"]
    assert not (tmp_path / "frontend" / "dist").exists()
    assert not (tmp_path / "var" / "classic-lan" / "runtime" / "classic-release.lock").exists()
    assert "MAIN_COMMIT_CHANGED" in MODULE.assess(tmp_path, "d" * 40)["blockers"]
    assert "API_REVISION_CHANGED" in MODULE.assess(tmp_path, expected_api="f" * 64)["blockers"]


def test_paid_or_cut_work_always_blocks_release(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, paused=True, cut_busy=True)
    result = MODULE.publish_frontend(tmp_path, head, "a" * 64, 15)
    assert not result["ready"]
    assert "CUT_JOBS_IN_FLIGHT" in result["blockers"]
    assert not (tmp_path / "frontend" / "dist").exists()


def test_metadata_only_git_status_is_not_a_source_change(monkeypatch, tmp_path):
    head = setup_check(monkeypatch)
    original_git = MODULE.git

    def git_with_metadata_only(root, *args):
        if args == ("status", "--porcelain"):
            return " M frontend/vite.config.js"
        if args in (("diff", "--numstat", "HEAD", "--"),
                    ("ls-files", "--others", "--exclude-standard")):
            return ""
        return original_git(root, *args)

    monkeypatch.setattr(MODULE, "git", git_with_metadata_only)
    assert MODULE.assess(tmp_path, head)["ready"]


def test_release_lock_refuses_parallel_publish(monkeypatch, tmp_path):
    with MODULE.release_lock(tmp_path):
        with pytest.raises(RuntimeError, match="release lock exists"):
            with MODULE.release_lock(tmp_path):
                pass
    assert not (tmp_path / "var" / "classic-lan" / "runtime" / "classic-release.lock").exists()


def test_frontend_only_allows_busy_cut_without_pausing(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, cut_busy=True)
    result = MODULE.assess(tmp_path, head, "a" * 64, frontend_only=True)
    assert result["ready"]
    assert result["admissionPaused"] is False
    assert "CUT_JOBS_IN_FLIGHT" in result["taskWarnings"]
    assert not MODULE.assess(tmp_path, head)["ready"]
    assert not MODULE.assess(tmp_path, head, "f" * 64, frontend_only=True)["ready"]


def test_frontend_only_refuses_changed_live_index(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, cut_busy=True)
    result = MODULE.publish_frontend(tmp_path, head, "a" * 64, 15,
                                     frontend_only=True, expected_old_frontend="d" * 64, change_base="base")
    assert not result["ready"]
    assert "FRONTEND_BASELINE_CHANGED" in result["blockers"]
    assert not (tmp_path / "frontend" / "dist").exists()


def test_hot_release_executes_with_busy_tasks_and_preserves_strict_gate(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace
    head = setup_check(monkeypatch, cut_busy=True)
    dist = tmp_path / "frontend" / "dist"
    stage = tmp_path / "frontend" / "dist-build"
    dist.mkdir(parents=True)
    stage.mkdir()
    old, new = b"old-page", b"new-page"
    (dist / "index.html").write_bytes(old)
    (stage / "index.html").write_bytes(new)
    old_sha = hashlib.sha256(old).hexdigest()
    new_sha = hashlib.sha256(new).hexdigest()
    def live(root):
        value = fake_live(head)
        value["frontendIndexSha256"] = hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()
        return value
    monkeypatch.setattr(MODULE, "report", live)
    monkeypatch.setattr(MODULE.shutil, "which", lambda name: name)
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        if args[0] == "node":
            (dist / "index.html").write_bytes(new)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(MODULE.subprocess, "run", execute)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return (dist / "index.html").read_bytes()
    monkeypatch.setattr(MODULE, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **k: Response()))
    result = MODULE.publish_frontend(tmp_path, head, "a" * 64, 15,
        frontend_only=True, expected_old_frontend=old_sha, change_base="base")
    assert result["ready"] and result["frontendPublished"]
    assert result["expectedFrontendSha256"] == new_sha == result["servedFrontendSha256"]
    assert "CUT_JOBS_IN_FLIGHT" in result["taskWarnings"]
    assert result["admissionPaused"] is False
    assert Path(result["rollbackIndex"]).read_bytes() == old
    assert len(calls) == 2
    assert calls[0][1:] == ["run", "build"]
    assert calls[1][-1] == "--apply"


def test_frontend_hot_publish_refuses_mixed_committed_changes(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, cut_busy=True)
    monkeypatch.setattr(MODULE, "committed_changes", lambda root, base: {
        "frontendHotEligible": False, "target": head, "mode": "worker-handoff-required",
    })
    result = MODULE.publish_frontend(
        tmp_path, head, "a" * 64, 15,
        frontend_only=True, expected_old_frontend="b" * 64, change_base="base",
    )
    assert not result["ready"]
    assert "FRONTEND_CHANGE_SCOPE_NOT_VERIFIED" in result["blockers"]
    assert not (tmp_path / "frontend" / "dist").exists()


def test_release_plan_auto_selects_frontend_and_denies_worker_hot_publish(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, cut_busy=True)
    monkeypatch.setattr(MODULE, "committed_changes", lambda root, base: {
        "frontendHotEligible": True, "target": head, "mode": "frontend-hot",
        "paths": ["frontend/src/App.tsx"],
    })
    result = MODULE.plan_release(tmp_path, base="approved-old")
    assert result["canHotPublishFrontend"] is True
    assert result["online"]["taskWarnings"] == ["CUT_JOBS_IN_FLIGHT"]
    monkeypatch.setattr(MODULE, "committed_changes", lambda root, base: {
        "frontendHotEligible": False, "target": head,
        "mode": "worker-handoff-required", "paths": ["scripts/classic_runner.py"],
    })
    result = MODULE.plan_release(tmp_path, base="approved-old")
    assert result["canHotPublishFrontend"] is False
    assert "NOT_AUTOMATICALLY_HOT_DEPLOYABLE" in result["blockers"]


def test_hot_release_refuses_backend_config_drift(monkeypatch, tmp_path):
    head = setup_check(monkeypatch, cut_busy=True)
    before = fake_live(head)
    after = dict(before, activeConfigRevision="changed")
    states = iter([before, after])
    monkeypatch.setattr(MODULE, "report", lambda root: next(states))
    monkeypatch.setattr(MODULE.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(MODULE.subprocess, "run", lambda args, **kwargs: calls.append(args))
    result = MODULE.publish_frontend(tmp_path, head, "a" * 64, 15,
        frontend_only=True, expected_old_frontend="b" * 64, change_base="base")
    assert not result["ready"]
    assert "LIVE_BASELINE_CHANGED:activeConfigRevision" in result["blockers"]
    assert len(calls) == 1
