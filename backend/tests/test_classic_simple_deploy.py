"""The short deploy command reuses the real release gates and receipt logic."""
import subprocess
from pathlib import Path

from test_classic_isolated_frontend import release, mod
from test_classic_live_frontend_plan import proven_receipt
import classic_simple_deploy as simple
import classic_release_manager as manager


def setup_live(release, monkeypatch):
    root, _, pins = release
    proven_receipt(root, pins)
    monkeypatch.setattr(simple.release, "report", mod.report)
    return root, pins


def test_preview_and_no_change_do_not_write(release, monkeypatch):
    root, pins = setup_live(release, monkeypatch)
    plan = simple.deploy(root)
    assert plan["action"] == "preview" and not plan["deployed"]
    assert not manager.status(root)
    assert simple.deploy(root, apply=True)["blockers"] == ["REVIEW_EXACT_DIFF_AND_API_COMPATIBILITY_FIRST"]
    proven_receipt(root, pins, artifactSourceHead=pins["candidate_head"], approvedFrontendHead=pins["main"])
    result = simple.deploy(root, apply=True)
    assert result["action"] == "no-change" and result["backendPending"]
    assert not manager.status(root)


def test_one_command_runs_real_isolated_publish(release, monkeypatch):
    root, pins = setup_live(release, monkeypatch)
    real_run = subprocess.run
    npm_calls = []
    def run(args, **kwargs):
        if args[0] == "npm-test":
            npm_calls.append(args[1])
            if args[1] == "run":
                stage = Path(kwargs["cwd"]) / "dist-build"
                (stage / "assets").mkdir(parents=True)
                (stage / "assets/index-new.js").write_bytes(b"new asset")
                (stage / "index.html").write_text('<script src="/assets/index-new.js"></script>')
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)
    monkeypatch.setattr(simple.shutil, "which", lambda _: "npm-test")
    monkeypatch.setattr(simple.subprocess, "run", run)
    result = simple.deploy(root, apply=True, reviewed=True, expected_main=pins["main"])
    assert result["ready"] and result["deployed"], result
    assert npm_calls == ["ci", "run"]
    assert result["outcome"]["result"]["receipt"]
    assert simple.deploy(root)["action"] == "no-change"


def test_unhealthy_and_stale_review_block_without_queue(release, monkeypatch):
    root, pins = setup_live(release, monkeypatch)
    assert simple.deploy(root, apply=True, reviewed=True, expected_main="f"*40)["blockers"] == ["MAIN_CHANGED_SINCE_REVIEW"]
    monkeypatch.setattr(simple.release, "report", lambda _: {"ok": False})
    assert not simple.deploy(root, apply=True, reviewed=True)["ready"]
    assert not manager.status(root)


def test_does_not_claim_another_sessions_job(release, monkeypatch):
    root, pins = setup_live(release, monkeypatch)
    first = manager.enqueue(root, root.parent / "candidate", pins)
    assert simple.deploy(root, apply=True, reviewed=True)["blockers"] == ["ANOTHER_RELEASE_PENDING_OR_NEEDS_REVIEW"]
    assert manager.claim(root, "different-id") is None
    assert manager.status(root)[0]["id"] == first["id"]
    assert manager.status(root)[0]["state"] == "queued"


def test_api_only_requires_reviewed_target_pin(release, monkeypatch):
    root, _, pins = release
    state = mod.report(root)
    state.update(standbyApiPort=8003, standbyApiSourceRevision="b"*64, activeConfigRevision="c"*64)
    monkeypatch.setattr(simple.release, "report", lambda _: state)
    monkeypatch.setattr(simple, "http_digest", lambda: pins["old"])
    assert simple.deploy(root, api_only=True)["action"] == "preview"
    assert simple.deploy(root, api_only=True, apply=True, reviewed=True)["blockers"] == ["PIN_REVIEWED_API_TARGET_FIRST"]
    assert simple.deploy(root, api_only=True, apply=True, reviewed=True, expected_api="d"*64)["blockers"] == ["API_CHANGED_SINCE_REVIEW"]
    assert not manager.status(root)
