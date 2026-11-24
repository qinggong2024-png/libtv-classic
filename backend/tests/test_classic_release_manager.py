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
