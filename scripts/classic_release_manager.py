"""Classic release coordinator V1: durable, explicitly approved frontend queue.

This is separate from the production task database. Existing release gates
remain authoritative. No daemon, task admission changes or automatic retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from classic_release import ROOT, git, release_lock
from classic_isolated_frontend import SHA40, SHA64, live_frontend_baseline, publish_isolated, tree
from classic_active_release_status import report


def now():
    return datetime.now(UTC).isoformat()


def connect(root):
    directory = root / "var/classic-lan/runtime/release-manager"
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "queue.sqlite3", timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS releases (
        id TEXT PRIMARY KEY, intent TEXT NOT NULL, state TEXT NOT NULL,
        created TEXT NOT NULL, updated TEXT NOT NULL, result TEXT
    )""")
    db.commit()
    return db


def enqueue(root, candidate, pins):
    if not all(isinstance(pins.get(k), str) and SHA40.fullmatch(pins[k])
               for k in ("base", "approved", "main", "candidate_head")):
        raise ValueError("Full approved Git identities required")
    if not all(isinstance(pins.get(k), str) and SHA64.fullmatch(pins[k])
               for k in ("api", "old")):
        raise ValueError("Full live API and frontend fingerprints required")
    intent = json.dumps({"candidate": str(candidate.resolve()), "pins": pins},
                        sort_keys=True, separators=(",", ":"))
    identifier = hashlib.sha256(intent.encode()).hexdigest()
    with closing(connect(root)) as db, db:
        db.execute("INSERT OR IGNORE INTO releases VALUES (?, ?, 'queued', ?, ?, NULL)",
                   (identifier, intent, now(), now()))
        return dict(db.execute("SELECT * FROM releases WHERE id=?", (identifier,)).fetchone())


def status(root):
    if not (root / "var/classic-lan/runtime/release-manager/queue.sqlite3").exists():
        return []
    with closing(connect(root)) as db:
        return [dict(row) for row in db.execute("SELECT * FROM releases ORDER BY created, id")]


def claim(root):
    with closing(connect(root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM releases WHERE state IN ('running','needs-review')").fetchone():
            return None
        row = db.execute("SELECT * FROM releases WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE releases SET state='running',updated=? WHERE id=?", (now(), row["id"]))
        return dict(row)


def finish(root, identifier, state, result):
    with closing(connect(root)) as db, db:
        cur = db.execute("UPDATE releases SET state=?,updated=?,result=? WHERE id=? AND state='running'",
                         (state, now(), json.dumps(result, ensure_ascii=True), identifier))
        if cur.rowcount != 1:
            raise RuntimeError("Release claim lost; reconcile before retry")
    return {"id": identifier, "state": state, "result": result}


@contextmanager
def activation_lock(root):
    # Same ordering as classic_activity_frontend_release. Do not switch API
    # concurrently with the existing frontend publisher.
    from classic_blue_api import route_operator_lock
    with release_lock(root), route_operator_lock(root / "var/classic-lan/runtime"):
        yield


@contextmanager
def coordinator_lease(root):
    # OS-owned lock releases on process exit; the queue's durable running
    # state still requires reconciliation. Never unlink a held lock file.
    directory = root / "var/classic-lan/runtime/release-manager"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "coordinator.lock").open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_next(root, publisher=publish_isolated):
    with coordinator_lease(root):
        return run_claimed(root, publisher)


def run_claimed(root, publisher):
    row = claim(root)
    if row is None:
        return {"state": "idle-or-blocked", "nextAction": "inspect queue status"}
    identifier = row["id"]
    try:
        intent = json.loads(row["intent"])
        result = publisher(root, Path(intent["candidate"]), lock=activation_lock, **intent["pins"])
        if result.get("ready") is True and result.get("frontendPublished") is True and result.get("receipt"):
            state = "verified"
        elif result.get("ready") is False and not result.get("frontendPublished"):
            state = "blocked"
        else:
            state = "needs-review"
    except Exception as error:
        # A failure can occur after a live pointer change. Never retry blindly.
        state = "needs-review"
        result = {"errorType": type(error).__name__, "nextAction": "reconcile actual live index and receipt"}
    return finish(root, identifier, state, result)


def retry_blocked(root, identifier):
    # Explicit operator action, never retry verified or uncertain releases.
    with closing(connect(root)) as db, db:
        cur = db.execute("UPDATE releases SET state='queued',updated=? WHERE id=? AND state='blocked'",
                         (now(), identifier))
        if cur.rowcount != 1:
            raise ValueError("Only a blocked release can be requeued")
    return {"id": identifier, "state": "queued"}


def reconcile(root, identifier):
    # A live coordinator cannot be reconciled by another process. A stale
    # legacy release/route lock remains a manual safety stop.
    with coordinator_lease(root), activation_lock(root):
        with closing(connect(root)) as db:
            row = db.execute("SELECT * FROM releases WHERE id=? AND state IN ('running','needs-review')",
                             (identifier,)).fetchone()
            if row is None:
                raise ValueError("No interrupted or uncertain release to reconcile")
            pins = json.loads(row["intent"])["pins"]
            state = report(root)
            proof = live_frontend_baseline(root, state)
            if not proof:
                raise RuntimeError("Current live frontend provenance is not verified")
            current = state["frontendIndexSha256"]
            api = (state.get("activeApi") or {}).get("sourceRevision")
            # Do not infer an exact release from a matching frontend tree alone.
            if proof["artifactSourceHead"] == pins["candidate_head"] and api == pins["api"]:
                receipt_path = root / "var/classic-lan/runtime/release-history" / proof["receipt"]
                receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
                if (receipt.get("frontendOld") == pins["old"]
                        and receipt.get("mainHead") == pins["main"]
                        and receipt.get("approvedFrontendHead") == pins["approved"]):
                    outcome = "verified"
                else:
                    outcome = "reconciled"
            elif current == pins["old"] and tree(root, proof["base"]) == tree(root, pins["base"]):
                outcome = "blocked"
            else:
                outcome = "reconciled"
            result = {"liveFrontend": current, "liveReceipt": proof["receipt"],
                      "nextAction": "review current live plan; never replay an uncertain release"}
            with db:
                cur = db.execute("UPDATE releases SET state=?,updated=?,result=? WHERE id=? AND state IN ('running','needs-review')",
                                 (outcome, now(), json.dumps(result), identifier))
                if cur.rowcount != 1:
                    raise RuntimeError("Release reconciliation lost ownership")
            return {"id": identifier, "state": outcome, "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    submit = sub.add_parser("enqueue-frontend")
    submit.add_argument("--candidate-root", type=Path, required=True)
    for arg in ("change-base", "approved-frontend-head", "expected-main-head",
                "expected-candidate-head", "expected-api-revision", "expected-old-frontend"):
        submit.add_argument("--" + arg, required=True)
    submit.add_argument("--reviewed-api-compatible", action="store_true", required=True)
    run = sub.add_parser("run-next")
    run.add_argument("--apply", action="store_true", required=True)
    for name in ("retry-blocked", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("id")
        command.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    if args.command != "status" and git(ROOT, "branch", "--show-current") != "main":
        parser.error("Mutating commands must run from production main")
    if args.command == "status":
        result = status(ROOT)
    elif args.command == "enqueue-frontend":
        result = enqueue(ROOT, args.candidate_root, dict(base=args.change_base,
            approved=args.approved_frontend_head, main=args.expected_main_head,
            candidate_head=args.expected_candidate_head, api=args.expected_api_revision,
            old=args.expected_old_frontend))
    elif args.command == "retry-blocked":
        result = retry_blocked(ROOT, args.id)
    elif args.command == "reconcile":
        result = reconcile(ROOT, args.id)
    else:
        result = run_next(ROOT)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if isinstance(result, dict) and result.get("state") in {"blocked", "needs-review", "idle-or-blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
