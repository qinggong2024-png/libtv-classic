"""Classic release coordinator: durable, explicitly approved component releases.

This is separate from the production task database. Existing release gates
remain authoritative. No daemon, task admission changes or automatic retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import shutil
import subprocess
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from classic_release import ROOT, git, activation_lock, release_lock, route_activation_lock, ActivationBusy
from classic_isolated_frontend import SHA40, SHA64, clean, http_digest, live_frontend_baseline, publish_isolated, record_receipt, tree
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
    return store_intent(root, {"candidate": str(candidate.resolve()), "pins": pins})


def store_intent(root, value):
    intent = json.dumps(value, sort_keys=True, separators=(",", ":"))
    identifier = hashlib.sha256(intent.encode()).hexdigest()
    with closing(connect(root)) as db, db:
        db.execute("INSERT OR IGNORE INTO releases VALUES (?, ?, 'queued', ?, ?, NULL)",
                   (identifier, intent, now(), now()))
        return dict(db.execute("SELECT * FROM releases WHERE id=?", (identifier,)).fetchone())


def enqueue_api(root, pins):
    if (pins.get("current") not in (8002, 8003) or pins.get("target") not in (8002, 8003)
            or pins["current"] == pins["target"]):
        raise ValueError("Distinct Classic slots required")
    if not all(isinstance(pins.get(k), str) and SHA64.fullmatch(pins[k])
               for k in ("active", "target_revision", "frontend", "config")):
        raise ValueError("Full API, frontend and config fingerprints required")
    if not isinstance(pins.get("schema"), str) or not pins["schema"]:
        raise ValueError("Verified schema identity required")
    return store_intent(root, {"kind": "online-api-only", "pins": pins})


def api_matches(state, pins, *, target=False):
    active = state.get("activeApi") or {}
    return (state.get("ok") is True and active.get("diskMatchesRunning") is True
            and state.get("activeApiPort") == pins["target" if target else "current"]
            and active.get("sourceRevision") == pins["target_revision" if target else "active"]
            and state.get("frontendIndexSha256") == pins["frontend"]
            and state.get("activeConfigRevision") == pins["config"]
            and state.get("activeSchemaRevision") == pins["schema"]
            and state.get("admissionPaused") is False)


def switch_reviewed_api(root, pins):
    # Node owns api-routing.lock; never acquire it twice. Frontend publishers
    # share classic-release.lock and route lock, and the original API-only
    # compatibility, durable ownership and installed-slot proof remain mandatory.
    with release_lock(root):
        before = report(root)
        if (not api_matches(before, pins) or before.get("standbyApiSourceRevision") != pins["target_revision"]
                or http_digest() != pins["frontend"]):
            return {"ready": False, "blockers": ["LIVE_API_BASELINE_CHANGED"]}
        proof = live_frontend_baseline(root, before)
        node = shutil.which("node")
        if not node:
            return {"ready": False, "blockers": ["NODE_UNAVAILABLE"]}
        cmd = [node, str(root / "scripts/classic_api_hot_switch.mjs"), "switch",
               "--expected-current", str(pins["current"]), "--target", str(pins["target"]),
               "--expected-current-revision", pins["active"], "--expected-source-revision", pins["target_revision"],
               "--route-file", str(root / "var/classic-lan/runtime/api-routing.json"), "--online-api-only"]
        # An unsuccessful dry-run has not switched anything. An exception after
        # --apply is uncertain and must be reconciled rather than replayed.
        check = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=90)
        if check.returncode:
            return {"ready": False, "blockers": ["API_ONLY_COMPATIBILITY_PROOF_FAILED"]}
        switched = subprocess.run(cmd + ["--apply"], cwd=root, capture_output=True,
                                  text=True, check=True, timeout=90)
        receipt = json.loads(switched.stdout)
        # The Node operator has released its route lock. Reacquire it for
        # acceptance; if another operator won the race, fail closed.
        with route_activation_lock(root / "var/classic-lan/runtime"):
            after = report(root)
            if (receipt.get("action") != "switched" or receipt.get("from") != pins["current"]
                    or receipt.get("to") != pins["target"] or not api_matches(after, pins, target=True)
                    or http_digest() != pins["frontend"]):
                raise RuntimeError("API_POST_SWITCH_VERIFICATION_FAILED")
            result = {"ready": True, "apiSwitched": True, "switchReceipt": receipt,
                      "activeApiRevision": pins["target_revision"], "frontendIndexSha256": pins["frontend"],
                      "activeConfigRevision": pins["config"], "activeSchemaRevision": pins["schema"]}
            # Preserve frontend provenance across an explicitly approved API-only
            # switch, not a fabricated frontend publication. No prior proof means
            # no continuity receipt; require separate frontend reconciliation.
            if proof:
                current_main = (after.get("mainCheckout") or {}).get("gitHead")
                git(root, "merge-base", "--is-ancestor", proof["base"], current_main)
                if tree(root, proof["artifactSourceHead"]) != tree(root, proof["base"]):
                    raise RuntimeError("FRONTEND_PROVENANCE_CHANGED")
                result["compatibilityReceipt"] = record_receipt(root, {
                    "mode": "api-only-compatibility", "frontendPublished": False,
                    "frontendOld": pins["frontend"], "frontendNew": pins["frontend"],
                    "servedFrontendSha256": pins["frontend"], "artifactSourceHead": proof["artifactSourceHead"],
                    "approvedFrontendHead": proof["base"], "mainHead": current_main,
                    "priorReceipt": proof["receipt"], "previousApiRevision": pins["active"],
                    "activeApiRevision": pins["target_revision"], "activeApiPort": pins["target"],
                    "activeConfigRevision": pins["config"], "activeSchemaRevision": pins["schema"],
                    "apiSwitchReceipt": receipt,
                })
            else:
                result["warnings"] = ["FRONTEND_PROVENANCE_RECONCILIATION_REQUIRED"]
            return result


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


def run_next(root, publisher=publish_isolated, api_publisher=switch_reviewed_api):
    with coordinator_lease(root):
        return run_claimed(root, publisher, api_publisher)


def run_claimed(root, publisher, api_publisher):
    row = claim(root)
    if row is None:
        return {"state": "idle-or-blocked", "nextAction": "inspect queue status"}
    identifier = row["id"]
    try:
        intent = json.loads(row["intent"])
        if intent.get("kind") == "online-api-only":
            result = api_publisher(root, intent["pins"])
            published = result.get("apiSwitched") is True
            evidence = result.get("switchReceipt")
        else:
            result = publisher(root, Path(intent["candidate"]), lock=activation_lock,
                               allow_main_advance=True, **intent["pins"])
            published = result.get("frontendPublished") is True
            evidence = result.get("receipt")
        if result.get("ready") is True and published and evidence:
            state = "verified"
        elif result.get("ready") is False and not published:
            state = "blocked"
        else:
            state = "needs-review"
    except ActivationBusy:
        state = "blocked"
        result = {"ready": False, "blockers": ["PRODUCTION_ACTIVATION_BUSY"],
                  "nextAction": "retry-blocked after current release completes"}
    except Exception as error:
        # A failure can occur after a live pointer change. Never retry blindly.
        state = "needs-review"
        result = {"errorType": type(error).__name__, "nextAction": "reconcile actual live component and receipt"}
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
            intent = json.loads(row["intent"])
            pins = intent["pins"]
            state = report(root)
            if intent.get("kind") == "online-api-only":
                if (not (api_matches(state, pins) or api_matches(state, pins, target=True))
                        or http_digest() != pins["frontend"]):
                    raise RuntimeError("Live API identity cannot be reconciled")
                outcome = "reconciled" if api_matches(state, pins, target=True) else "blocked"
                result = {"liveApiPort": state["activeApiPort"], "liveApiRevision": state["activeApi"]["sourceRevision"],
                          "nextAction": "review installed API and current release plan; never replay uncertainty"}
                with db:
                    db.execute("UPDATE releases SET state=?,updated=?,result=? WHERE id=? AND state IN ('running','needs-review')",
                               (outcome, now(), json.dumps(result), identifier))
                return {"id": identifier, "state": outcome, "result": result}
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
                        and (receipt.get("mainHead") == pins["main"]
                             or receipt.get("requestedMainHead") == pins["main"])
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


def integrate_main(root, expected_main, candidate_head):
    """Promote an already-tested integration commit, never merge untested code."""
    if not all(isinstance(x, str) and SHA40.fullmatch(x) for x in (expected_main, candidate_head)):
        raise ValueError("Full tested integration pins required")
    with activation_lock(root):
        if (git(root, "branch", "--show-current") != "main" or not clean(root)
                or git(root, "rev-parse", "HEAD") != expected_main):
            return {"ready": False, "blockers": ["MAIN_CHANGED_OR_DIRTY"]}
        try:
            git(root, "merge-base", "--is-ancestor", expected_main, candidate_head)
        except subprocess.CalledProcessError:
            return {"ready": False, "blockers": ["INTEGRATION_REBASE_AND_RETEST_REQUIRED"]}
        git(root, "merge", "--ff-only", candidate_head)
        if git(root, "rev-parse", "HEAD") != candidate_head or not clean(root):
            raise RuntimeError("Integration result changed; inspect source")
        return {"ready": True, "mainHead": candidate_head, "sourceSynchronized": True,
                "productionActivated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    integrate = sub.add_parser("integrate")
    integrate.add_argument("--expected-main", required=True)
    integrate.add_argument("--tested-candidate", required=True)
    integrate.add_argument("--apply", action="store_true", required=True)
    submit = sub.add_parser("enqueue-frontend")
    submit.add_argument("--candidate-root", type=Path, required=True)
    for arg in ("change-base", "approved-frontend-head", "expected-main-head",
                "expected-candidate-head", "expected-api-revision", "expected-old-frontend"):
        submit.add_argument("--" + arg, required=True)
    submit.add_argument("--reviewed-api-compatible", action="store_true", required=True)
    api = sub.add_parser("enqueue-api")
    api.add_argument("--current", type=int, required=True)
    api.add_argument("--target", type=int, required=True)
    for arg in ("active", "target-revision", "frontend", "config", "schema"):
        api.add_argument("--" + arg, required=True)
    api.add_argument("--reviewed-api-compatible", action="store_true", required=True)
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
    elif args.command == "integrate":
        result = integrate_main(ROOT, args.expected_main, args.tested_candidate)
    elif args.command == "enqueue-frontend":
        result = enqueue(ROOT, args.candidate_root, dict(base=args.change_base,
            approved=args.approved_frontend_head, main=args.expected_main_head,
            candidate_head=args.expected_candidate_head, api=args.expected_api_revision,
            old=args.expected_old_frontend))
    elif args.command == "enqueue-api":
        result = enqueue_api(ROOT, dict(current=args.current, target=args.target, active=args.active,
            target_revision=args.target_revision, frontend=args.frontend, config=args.config, schema=args.schema))
    elif args.command == "retry-blocked":
        result = retry_blocked(ROOT, args.id)
    elif args.command == "reconcile":
        result = reconcile(ROOT, args.id)
    else:
        result = run_next(ROOT)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if isinstance(result, dict) and (result.get("ready") is False or result.get("state") in {"blocked", "needs-review", "idle-or-blocked"}) else 0


if __name__ == "__main__":
    raise SystemExit(main())
