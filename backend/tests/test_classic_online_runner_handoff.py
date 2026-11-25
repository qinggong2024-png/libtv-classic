import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from classic_online_runner_handoff import evidence


def test_receipt_bound_runner_evidence(tmp_path):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE generation_attempt(id TEXT,provider TEXT,status TEXT,task_id TEXT);
        CREATE TABLE generation_submission_attempt(attempt_id TEXT,status TEXT,provider_task_id TEXT);
        CREATE TABLE generation_command(status TEXT,attempt_id TEXT);
        CREATE TABLE outbox_event(event_type TEXT);
        INSERT INTO generation_attempt VALUES('a','kling_official_api','RUNNING','cloud-original');
        INSERT INTO generation_submission_attempt VALUES('a','ACCEPTED','cloud-original');
        INSERT INTO generation_command VALUES('PROCESS_STARTED','a');
        """)
    originals, _ = evidence(db, 0)
    assert originals == {"a": "cloud-original"}
    with sqlite3.connect(db) as c:
        c.execute("UPDATE generation_attempt SET status='SUCCEEDED'")
        c.execute("UPDATE generation_command SET status='DONE'")
    assert evidence(db, 0, originals, drained=True)[0] == {}
    with sqlite3.connect(db) as c:
        c.execute("UPDATE generation_attempt SET task_id='wrong'")
    with pytest.raises(RuntimeError, match="identity changed"):
        evidence(db, 0, originals)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE generation_attempt SET status='SUBMITTING'")
    with pytest.raises(RuntimeError, match="unresolved"):
        evidence(db, 0)


def test_exact_reviewed_reliability_runner_transition():
    from classic_online_runner_handoff import (
        OLD, NEW, API, RELIABILITY_RUNNER, RELIABILITY_API_SLOTS, reviewed_transition,
    )
    assert reviewed_transition(OLD, NEW) == {8002: API, 8003: API}
    assert reviewed_transition(NEW, RELIABILITY_RUNNER) == RELIABILITY_API_SLOTS
    with pytest.raises(RuntimeError, match="unreviewed"):
        reviewed_transition(NEW, "e" * 64)
    with pytest.raises(RuntimeError, match="unreviewed"):
        reviewed_transition(OLD, RELIABILITY_RUNNER)


def test_exact_sol400_emergency_transition(monkeypatch):
    import classic_online_runner_handoff as h
    assert h.reviewed_transition(h.RELIABILITY_RUNNER, h.SOL400_RUNNER) == h.SOL400_API_SLOTS
    with pytest.raises(RuntimeError, match="unreviewed"):
        h.reviewed_transition(h.NEW, h.SOL400_RUNNER)
    route = {"activeApiPort": 8002, "inFlightByPort": {"8003": 0}, "pendingRouteSelections": 0}
    monkeypatch.setattr(h, "web_route", lambda: route)
    monkeypatch.setattr(h, "read_manifest", lambda: {"pid": 123})
    monkeypatch.setattr(h, "green_process", lambda m: None)
    assert h.verify_route_scope(h.SOL400_API_SLOTS) == route
    route["inFlightByPort"]["8003"] = 1
    with pytest.raises(RuntimeError, match="inactive green"):
        h.verify_route_scope(h.SOL400_API_SLOTS)
    route["inFlightByPort"]["8003"] = 0
    monkeypatch.setattr(h, "green_process", lambda m: object())
    with pytest.raises(RuntimeError, match="inactive green"):
        h.verify_route_scope(h.SOL400_API_SLOTS)
    route["activeApiPort"] = 8003
    with pytest.raises(RuntimeError, match="active API"):
        h.verify_route_scope(h.SOL400_API_SLOTS)


def test_exact_lossless_jpeg_runner_transition():
    import classic_online_runner_handoff as h

    assert h.reviewed_transition(h.SOL400_RUNNER, h.SOL_JPEG_RUNNER) == h.SOL400_API_SLOTS
    with pytest.raises(RuntimeError, match="unreviewed"):
        h.reviewed_transition(h.RELIABILITY_RUNNER, h.SOL_JPEG_RUNNER)
    with pytest.raises(RuntimeError, match="unreviewed"):
        h.reviewed_transition(h.SOL400_RUNNER, "f" * 64)


def test_exact_sol_stream_runner_transition():
    import classic_online_runner_handoff as h
    assert h.reviewed_transition(h.SOL_JPEG_RUNNER, h.SOL_STREAM_RUNNER) == h.SOL_STREAM_API_SLOTS
    assert set(h.SOL_STREAM_API_SLOTS) == {8002, 8003}
    with pytest.raises(RuntimeError, match="unreviewed"):
        h.reviewed_transition(h.SOL400_RUNNER, h.SOL_STREAM_RUNNER)
    with pytest.raises(RuntimeError, match="unreviewed"):
        h.reviewed_transition(h.SOL_JPEG_RUNNER, "f" * 64)


@pytest.fixture
def compiler_pair(tmp_path, monkeypatch):
    import classic_online_runner_handoff as h

    old, new = "a" * 64, "b" * 64
    base, candidate = tmp_path / "old", tmp_path / "new"
    base.mkdir()

    def git(root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git(base, "init", "-q")
    git(base, "config", "user.email", "offline@example.invalid")
    git(base, "config", "user.name", "Offline Test")
    path = sorted(h.COMPILER_RUNTIME_PATHS)[0]
    (base / path).parent.mkdir(parents=True)
    (base / path).write_text("old\n", encoding="utf-8")
    (base / "unrelated.py").write_text("unchanged\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-qm", "base")
    git(base, "worktree", "add", "--detach", str(candidate), "HEAD")
    (candidate / path).write_text("new\n", encoding="utf-8")
    git(candidate, "add", path)
    git(candidate, "commit", "-qm", "compiler")
    monkeypatch.setattr(h, "source_fingerprint", lambda p: old if p == base else new)
    monkeypatch.setattr(h, "verify_worker_compatibility", lambda a, b: None)
    review = {"old": old, "new": new, "commit": git(candidate, "rev-parse", "HEAD"),
              "apiSlots": {8002: "c" * 64, 8003: "d" * 64}}
    return h, base, candidate, review, git


def test_reviewed_compiler_pair_is_exact_and_requires_both_slots(compiler_pair):
    h, base, candidate, review, _ = compiler_pair
    def check(r):
        return h.reviewed_compiler_transition(base, candidate, old=review["old"],
                                               new=review["new"], review=r)
    assert check(review) == review["apiSlots"]
    for changed in (
        {**review, "commit": "e" * 40},
        {**review, "old": "e" * 64},
        {**review, "apiSlots": {8002: "c" * 64}},
    ):
        with pytest.raises(RuntimeError):
            check(changed)


def test_reviewed_compiler_pair_refuses_dirty_or_unrelated_changes(compiler_pair):
    h, base, candidate, review, git = compiler_pair
    def check():
        return h.reviewed_compiler_transition(base, candidate, old=review["old"],
                                               new=review["new"], review=review)
    (candidate / "unrelated.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not clean"):
        check()
    git(candidate, "add", "unrelated.py")
    git(candidate, "commit", "-qm", "unrelated")
    review["commit"] = git(candidate, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="not a compiler-only change"):
        check()


def test_reviewed_compiler_pair_refuses_changed_runtime_fingerprint(compiler_pair, monkeypatch):
    h, base, candidate, review, _ = compiler_pair
    monkeypatch.setattr(h, "source_fingerprint", lambda p: "f" * 64)
    with pytest.raises(RuntimeError, match="source changed"):
        h.reviewed_compiler_transition(base, candidate, old=review["old"],
                                      new=review["new"], review=review)


def test_compiler_update_rejects_changed_api_contract(tmp_path, monkeypatch):
    import classic_online_runner_handoff as h
    runner, api = tmp_path / "runner", tmp_path / "api"
    runner.mkdir()
    api.mkdir()
    monkeypatch.setattr(h, "verify_worker_compatibility", lambda a, b: None)
    monkeypatch.setattr(h, "PROTECTED", ("contract.py",))
    (runner / "contract.py").write_text("same", encoding="utf-8")
    (api / "contract.py").write_text("same", encoding="utf-8")
    h.verify_compiler_api_contract(runner, api)
    (api / "contract.py").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="API/Runner contract changed"):
        h.verify_compiler_api_contract(runner, api)


@pytest.mark.parametrize("mutation,match", [
    ("UPDATE generation_attempt SET status='SUBMITTING'", "unresolved"),
    ("UPDATE generation_submission_attempt SET provider_task_id='wrong'", "receipt"),
    ("UPDATE generation_command SET status='LAUNCH_STARTED'", "not safe"),
    ("INSERT INTO generation_submission_attempt VALUES('a','ACCEPTED','cloud-original')", "receipt"),
    ("UPDATE generation_attempt SET status='UNKNOWN_AFTER_SUBMIT'", "UNKNOWN increased"),
])
def test_online_evidence_fails_closed_before_handoff(tmp_path, mutation, match):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE generation_attempt(id TEXT,provider TEXT,status TEXT,task_id TEXT);
        CREATE TABLE generation_submission_attempt(attempt_id TEXT,status TEXT,provider_task_id TEXT);
        CREATE TABLE generation_command(status TEXT,attempt_id TEXT);
        CREATE TABLE outbox_event(event_type TEXT);
        INSERT INTO generation_attempt VALUES('a','kling_official_api','RUNNING','cloud-original');
        INSERT INTO generation_submission_attempt VALUES('a','ACCEPTED','cloud-original');
        INSERT INTO generation_command VALUES('PROCESS_STARTED','a');
        """)
        c.execute(mutation)
    with pytest.raises(RuntimeError, match=match):
        evidence(db, 0)
