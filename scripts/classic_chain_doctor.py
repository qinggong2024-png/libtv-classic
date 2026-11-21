"""Read-only chain triage. Never loads credentials, contacts providers or repairs data."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def diagnose(database_path: Path, *, now: datetime | None = None) -> dict:
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    report = {
        "checkedAt": checked_at.isoformat(),
        "readOnly": True,
        "status": "OK",
        "checks": [],
    }
    checks = report["checks"]

    def record(code, severity, count, action):
        checks.append(
            {
                "code": code,
                "severity": severity if count else "OK",
                "count": count,
                "action": action if count else "none",
            }
        )

    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as database:
            database.execute("PRAGMA query_only = ON")
            database.execute("BEGIN")
            tables = {
                r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {
                "runner_host",
                "generation_attempt",
                "generation_variant",
                "generation_command",
                "parent_task",
            }
            if not required.issubset(tables):
                raise ValueError("incomplete classic schema")
            instant = checked_at.isoformat()

            def count(query, parameters=()):
                return database.execute(query, parameters).fetchone()[0]

            live = count(
                "SELECT count(*) FROM runner_host WHERE status IN ('ONLINE','WARNING') "
                "AND (julianday(?) - julianday(last_heartbeat_at))*86400 BETWEEN -30 AND 60",
                (instant,),
            )
            record(
                "RUNNER_HEARTBEAT_STALE",
                "ERROR",
                int(not live),
                "Check classic_lan.py status and runner logs; do not replay paid submissions",
            )
            record(
                "PAID_SUBMISSION_UNCERTAIN",
                "WARNING",
                count(
                    "SELECT count(*) FROM generation_attempt WHERE status='UNKNOWN_AFTER_SUBMIT'"
                ),
                "Reconcile provider receipts and existing task IDs; never auto-submit again",
            )
            record(
                "RUNNING_HANDLE_MISSING",
                "ERROR",
                count(
                    "SELECT count(*) FROM generation_attempt WHERE status='RUNNING' "
                    "AND provider!='libtv' AND coalesce(task_id,'')=''"
                ),
                "Inspect the submission receipt without creating a replacement request",
            )
            record(
                "PROVIDER_POLL_STALE",
                "WARNING",
                count(
                    "SELECT count(*) FROM generation_attempt WHERE status='RUNNING' "
                    "AND provider!='libtv' AND coalesce(task_id,'')!='' "
                    "AND (julianday(?) - julianday("
                    "coalesce(last_polled_at,submitted_at,created_at)))"
                    "*86400 > 600",
                    (instant,),
                ),
                "Check runner and provider query health; resume queries using the original task ID",
            )
            record(
                "COMMAND_QUEUE_STALE",
                "WARNING",
                count(
                    "SELECT count(*) FROM generation_command WHERE status IN ('READY','CLAIMED') "
                    "AND (julianday(?) - julianday(updated_at))*86400 > 600",
                    (instant,),
                ),
                "Check pause, budget, provider authorization and capacity before intervention",
            )
            # Only the latest attempt belongs to the current delivery. Old failed
            # generations are historical evidence, not new incidents.
            delivery_join = (
                " FROM generation_variant v JOIN generation_attempt a ON a.variant_id=v.id "
                "JOIN parent_task p ON p.id=v.parent_task_id WHERE p.phase!='CANCELLED' "
                "AND a.id=(SELECT newest.id FROM generation_attempt newest "
                "WHERE newest.variant_id=v.id "
                "ORDER BY newest.created_at DESC,newest.id DESC LIMIT 1) "
                "AND a.status='SUCCEEDED' "
            )
            record(
                "DELIVERY_RETRY_PENDING",
                "WARNING",
                count(
                    "SELECT count(*)" + delivery_join + "AND v.download_status='FAILED' "
                    "AND a.last_error_code='DOWNLOAD_RETRYABLE'"
                ),
                "Runner retries delivery after cooldown; check disk, ffprobe and object storage",
            )
            record(
                "DELIVERY_STALLED",
                "ERROR",
                count(
                    "SELECT count(*)" + delivery_join + "AND v.download_status IN "
                    "('NOT_READY','QUEUED','DOWNLOADING') "
                    "AND (julianday(?) - julianday(a.updated_at))*86400 > 900",
                    (instant,),
                ),
                "Inspect delivery using the existing provider handle; never regenerate to download",
            )
            record(
                "PREPARATION_STALE",
                "WARNING",
                count(
                    "SELECT count(*) FROM parent_task WHERE phase IN ('ANALYZING','PREPARING') "
                    "AND (julianday(?) - julianday(updated_at))*86400 > 1800",
                    (instant,),
                ),
                "Inspect AI request timeout and confirmation recovery; preserve effective prompts",
            )
            optional_checks = [
                (
                    "api_idempotency_record",
                    "IDEMPOTENCY_CLAIM_STALE",
                    "response_status=0 AND (julianday(?) - julianday(created_at))*86400 > 1800",
                    "Inspect confirmation recovery and request ownership before releasing a claim",
                ),
                (
                    "image_generation_item",
                    "IMAGE_JOB_STALE",
                    "status IN ('PENDING','UPLOADING','SUBMITTING','RUNNING','DOWNLOADING') "
                    "AND (julianday(?) - julianday(updated_at))*86400 > 1800",
                    "Inspect image provider handle and recovery status; "
                    "do not duplicate submission",
                ),
                (
                    "cutflow_handoff",
                    "CUT_HANDOFF_STALE",
                    "status IN ('QUEUED','SUBMITTING','RUNNING','IMPORTING') "
                    "AND (julianday(?) - julianday(updated_at))*86400 > 1800",
                    "Use classic_cutflow_doctor.py with the handoff ID "
                    "for detailed read-only triage",
                ),
            ]
            for table, code, condition, action in optional_checks:
                if table in tables:
                    record(
                        code,
                        "WARNING",
                        count(
                            f"SELECT count(*) FROM {table} WHERE {condition}",
                            (instant,),
                        ),
                        action,
                    )
            report["status"] = (
                "ERROR"
                if any(c["severity"] == "ERROR" for c in checks)
                else "WARNING"
                if any(c["severity"] == "WARNING" for c in checks)
                else "OK"
            )
    except (OSError, sqlite3.Error, ValueError):
        # Error strings and arbitrary DB rows may contain paths, SQL or secrets.
        report["status"] = "ERROR"
        report["errorCode"] = "CLASSIC_DATABASE_UNREADABLE_OR_SCHEMA_INVALID"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "var/classic-lan/classic.db")
    args = parser.parse_args(argv)
    report = diagnose(args.database)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"OK": 0, "WARNING": 1, "ERROR": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
