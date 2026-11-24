"""Release queue tests: never access live tasks, providers or production."""
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_release_manager as mod


def intent(root, n=1):
    pins = dict(base="a"*40, approved="b"*40, main="c"*40,
                candidate_head=f"{n:040x}", api="d"*64, old="e"*64)
    return mod.enqueue(root, root / f"candidate-{n}", pins)


def test_duplicate_intent_is_one_durable_job(tmp_path):
    first = intent(tmp_path)
    assert intent(tmp_path)["id"] == first["id"]
    assert len(mod.status(tmp_path)) == 1


def test_concurrent_claims_only_one_owner(tmp_path):
    intent(tmp_path)
    intent(tmp_path, 2)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: mod.claim(tmp_path), range(6)))
    assert sum(row is not None for row in claims) == 1
    assert [row["state"] for row in mod.status(tmp_path)].count("running") == 1


def test_verified_requires_receipt_and_next_job_can_run(tmp_path):
    first = intent(tmp_path)
    second = intent(tmp_path, 2)
    def publish(root, candidate, **kw):
        return {"ready": True, "frontendPublished": True, "receipt": "verified.json"}
    assert mod.run_next(tmp_path, publish)["id"] == first["id"]
    assert mod.run_next(tmp_path, publish)["id"] == second["id"]
    assert all(row["state"] == "verified" for row in mod.status(tmp_path))
    assert intent(tmp_path)["state"] == "verified"


def test_stale_pins_are_blocked_not_retried(tmp_path):
    intent(tmp_path)
    result = mod.run_next(tmp_path, lambda *a, **k: {"ready": False, "blockers": ["MAIN_COMMIT_CHANGED"]})
    assert result["state"] == "blocked"
    assert mod.run_next(tmp_path)["state"] == "idle-or-blocked"


def test_uncertain_result_blocks_queue_until_reconciliation(tmp_path):
    intent(tmp_path)
    intent(tmp_path, 2)
    def publish(*a, **k):
        raise RuntimeError("unknown activation")
    assert mod.run_next(tmp_path, publish)["state"] == "needs-review"
    assert mod.run_next(tmp_path)["state"] == "idle-or-blocked"
    assert [row["state"] for row in mod.status(tmp_path)] == ["needs-review", "queued"]


def test_reconciliation_requires_verified_live_provenance(tmp_path, monkeypatch):
    row = intent(tmp_path)
    mod.claim(tmp_path)
    monkeypatch.setattr(mod, "activation_lock", lambda _: nullcontext())
    monkeypatch.setattr(mod, "report", lambda _: {"frontendIndexSha256": "e"*64})
    monkeypatch.setattr(mod, "live_frontend_baseline", lambda *a: None)
    with pytest.raises(RuntimeError, match="provenance"):
        mod.reconcile(tmp_path, row["id"])
    assert mod.status(tmp_path)[0]["state"] == "running"
    monkeypatch.setattr(mod, "live_frontend_baseline", lambda *a: {
        "base": "a"*40, "artifactSourceHead": "a"*40, "receipt": "old.json"})
    monkeypatch.setattr(mod, "tree", lambda _, ref: ref)
    assert mod.reconcile(tmp_path, row["id"])["state"] == "blocked"
    assert mod.retry_blocked(tmp_path, row["id"])["state"] == "queued"


def test_missing_full_pins_rejected(tmp_path):
    with pytest.raises(ValueError):
        mod.enqueue(tmp_path, tmp_path / "candidate", {"base": "a"})


def api_pins():
    return dict(current=8002, target=8003, active="a"*64, target_revision="b"*64,
                frontend="c"*64, config="d"*64, schema="schema")


def api_state(pins, target=False):
    return dict(ok=True, activeApiPort=pins["target" if target else "current"],
                activeApi=dict(diskMatchesRunning=True, sourceRevision=pins["target_revision" if target else "active"]),
                standbyApiSourceRevision=pins["target_revision"], frontendIndexSha256=pins["frontend"],
                activeConfigRevision=pins["config"], activeSchemaRevision=pins["schema"], admissionPaused=False)


def test_api_queue_reuses_existing_online_switch_and_checks_live(tmp_path, monkeypatch):
    import json
    import subprocess
    pins = api_pins()
    row = mod.enqueue_api(tmp_path, pins)
    assert mod.enqueue_api(tmp_path, pins)["id"] == row["id"]
    states = iter([api_state(pins), api_state(pins, target=True)])
    monkeypatch.setattr(mod, "report", lambda _: next(states))
    monkeypatch.setattr(mod, "http_digest", lambda: pins["frontend"])
    monkeypatch.setattr(mod.shutil, "which", lambda _: "node-test")
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"action": "switched", "from": 8002, "to": 8003}), "")
    monkeypatch.setattr(mod.subprocess, "run", run)
    result = mod.run_next(tmp_path)
    assert result["state"] == "verified"
    assert "--online-api-only" in calls[0] and "--apply" not in calls[0]
    assert "--apply" in calls[1]


def test_api_stale_identity_blocks_before_switch(tmp_path, monkeypatch):
    pins = api_pins()
    mod.enqueue_api(tmp_path, pins)
    state = api_state(pins)
    state["admissionPaused"] = True
    monkeypatch.setattr(mod, "report", lambda _: state)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("must not switch"))
    assert mod.run_next(tmp_path)["state"] == "blocked"


def test_api_uncertain_switch_never_auto_replays(tmp_path, monkeypatch):
    pins = api_pins()
    row = mod.enqueue_api(tmp_path, pins)
    mod.claim(tmp_path)
    monkeypatch.setattr(mod, "report", lambda _: api_state(pins, target=True))
    monkeypatch.setattr(mod, "http_digest", lambda: pins["frontend"])
    assert mod.reconcile(tmp_path, row["id"])["state"] == "reconciled"
    assert mod.run_next(tmp_path)["state"] == "idle-or-blocked"


def test_shared_activation_refuses_api_route_operator(tmp_path):
    from classic_blue_api import route_operator_lock
    runtime = tmp_path / "var/classic-lan/runtime"
    runtime.mkdir(parents=True)
    with route_operator_lock(runtime):
        with pytest.raises(mod.ActivationBusy):
            with mod.activation_lock(tmp_path):
                pytest.fail("must not activate concurrently")
    assert not (runtime / "classic-release.lock").exists()


def test_activation_busy_does_not_mark_unknown(tmp_path):
    intent(tmp_path)
    def busy(*a, **k):
        raise mod.ActivationBusy("busy")
    assert mod.run_next(tmp_path, busy)["state"] == "blocked"


def test_api_continuity_receipt_only_from_verified_existing_proof(tmp_path, monkeypatch):
    import json
    import subprocess
    pins = api_pins()
    base = "a"*40
    artifact = "b"*40
    states = [api_state(pins), api_state(pins, target=True)]
    states[1]["mainCheckout"] = {"gitHead": "c"*40}
    monkeypatch.setattr(mod, "report", lambda _: states.pop(0))
    monkeypatch.setattr(mod, "http_digest", lambda: pins["frontend"])
    monkeypatch.setattr(mod, "live_frontend_baseline", lambda *a: {"base": base, "artifactSourceHead": artifact, "receipt": "old.json"})
    monkeypatch.setattr(mod, "git", lambda *a: "")
    monkeypatch.setattr(mod, "tree", lambda *a: "same-tree")
    monkeypatch.setattr(mod.shutil, "which", lambda _: "node-test")
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({"action": "switched", "from": 8002, "to": 8003}), ""))
    result = mod.switch_reviewed_api(tmp_path, pins)
    receipt = json.loads(Path(result["compatibilityReceipt"]).read_text(encoding="utf-8"))
    assert receipt["frontendPublished"] is False
    assert receipt["priorReceipt"] == "old.json"
    assert receipt["artifactSourceHead"] == artifact
    assert receipt["activeApiRevision"] == pins["target_revision"]


def test_integrate_only_fast_forward_tested_candidate(tmp_path):
    from classic_isolated_frontend import git
    root = tmp_path / "main"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / ".gitignore").write_text("var/\n")
    (root / "source.txt").write_text("old")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    candidate = tmp_path / "candidate"
    git(root, "worktree", "add", "--detach", str(candidate), base)
    (candidate / "source.txt").write_text("new")
    git(candidate, "add", ".")
    git(candidate, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "tested")
    target = git(candidate, "rev-parse", "HEAD")
    assert not mod.integrate_main(root, "0"*40, target)["ready"]
    result = mod.integrate_main(root, base, target)
    assert result["sourceSynchronized"] and not result["productionActivated"]
    assert (root / "source.txt").read_text() == "new"
