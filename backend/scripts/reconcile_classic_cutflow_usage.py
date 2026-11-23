"""Conservative classic CUT usage reconciliation; dry-run by default.

Only verified CUT completed receipts + imported outputs can leave RESERVED for
ESTIMATED. This is a local budget classification, not a provider invoice.
No failed task is automatically refunded. Use --apply only with a fresh SQLite
online backup and after inspecting the dry-run counts. No network or CUT submits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _accepted_finished_receipt(task_id: str, manifest_path: str | None, nas_root: Path) -> bool:
    try:
        if not manifest_path or manifest_path != f"inbox/multi-{task_id}/manifest.json":
            return False
        manifest_file = nas_root / manifest_path
        receipt_file = nas_root / "outbox" / task_id / "status.json"
        if not manifest_file.is_file() or not receipt_file.is_file():
            return False
        payload = manifest_file.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        policy = receipt.get("policyReceipt")
        return (
            receipt.get("status") == "completed"
            and receipt.get("taskId") == task_id
            and receipt.get("manifestSha256") == digest
            and type(receipt.get("revision")) is int and receipt["revision"] >= 1
            and isinstance(receipt.get("batchId"), str)
            and bool(receipt["batchId"])
            and isinstance(policy, dict)
            and policy.get("state") == "accepted"
            and policy.get("taskId") == task_id
            and policy.get("manifestSha256") == digest
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def classify(task_id: str, task_status: str, manifest_path: str | None,
             output_json: str | None, nas_root: Path) -> str:
    if task_status != "PENDING_REVIEW":
        return "UNCERTAIN" if task_status == "FAILED" else "SKIP"
    try:
        outputs = json.loads(output_json or "[]")
        if not isinstance(outputs, list) or not outputs:
            return "MANUAL_REVIEW_MISSING_OUTPUT"
        if any(
            not isinstance(o, dict)
            or o.get("task_id") != task_id
            or not o.get("sha256")
            or not o.get("object_key")
            for o in outputs
        ):
            return "MANUAL_REVIEW_BAD_OUTPUT"
    except (TypeError, ValueError):
        return "MANUAL_REVIEW_BAD_OUTPUT"
    if not _accepted_finished_receipt(task_id, manifest_path, nas_root):
        return "MANUAL_REVIEW_NO_VERIFIED_RECEIPT"
    return "ESTIMATED"


def evaluate(db: Path, nas_root: Path, *, apply: bool, backup: Path | None,
             max_rows: int | None = None) -> dict:
    if apply and (not backup or not backup.is_file() or backup.resolve() == db.resolve()):
        raise ValueError("--apply requires an existing distinct --backup snapshot")
    uri = db.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, timeout=12)
    con.execute("PRAGMA busy_timeout=12000")
    try:
        rows = con.execute("""
            SELECT m.id, m.status, m.manifest_path, m.output_json,
                   a.id, a.status, a.amount_microusd
            FROM multi_cutflow_task AS m
            JOIN account_usage AS a ON a.operation_id = m.id AND a.kind = 'CUTFLOW'
            WHERE a.status = 'RESERVED'
              AND m.status IN ('PENDING_REVIEW','FAILED')
            ORDER BY m.id
        """).fetchall()
        if max_rows is not None:
            rows = rows[:max_rows]

        def check(row: tuple) -> str:
            task_id, state, manifest, output, *_ = row
            canonical = "-".join((task_id[:8], task_id[8:12], task_id[12:16],
                                  task_id[16:20], task_id[20:]))
            return classify(canonical, state, manifest, output, nas_root)

        # NAS is another machine; perform read-only checks concurrently and
        # never hold the SQLite write lock during remote filesystem I/O.
        with ThreadPoolExecutor(max_workers=8) as pool:
            verdicts = list(pool.map(check, rows))
        counts: Counter = Counter(verdicts)
        updated = 0
        if apply:
            con.execute("BEGIN IMMEDIATE")
        for row, verdict in zip(rows, verdicts, strict=True):
            task_id, state, manifest, output, usage_id, *_ = row
            if apply and verdict in {"ESTIMATED", "UNCERTAIN"}:
                result = con.execute("""
                    UPDATE account_usage SET status = ?
                    WHERE id = ? AND kind = 'CUTFLOW'
                      AND operation_id = ? AND status = 'RESERVED'
                      AND EXISTS (SELECT 1 FROM multi_cutflow_task m
                                  WHERE m.id = ? AND m.status = ?
                                    AND m.manifest_path = ? AND m.output_json = ?)
                """, (verdict, usage_id, task_id, task_id, state, manifest, output))
                if result.rowcount != 1:
                    raise RuntimeError("unexpected concurrent usage mutation")
                updated += 1
        if apply:
            con.commit()
        return {"eligible_rows": len(rows), "classifications": dict(counts),
                "changed": updated, "mode": "applied" if apply else "dry-run"}
    except Exception:
        if apply:
            con.rollback()
        raise
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--nas-root", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.db, args.nas_root, apply=args.apply,
                              backup=args.backup, max_rows=args.limit), sort_keys=True))


if __name__ == "__main__":
    main()
