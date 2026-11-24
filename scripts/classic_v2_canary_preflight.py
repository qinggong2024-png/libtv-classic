"""Read-only ALLWE classic V2 canary admission check.

Only aggregates task/attempt stages from the existing production database.
Never calls a provider, reads credentials, migrates schema, or mutates data.
An 'idle' result is advisory; deployment still requires explicit authorization.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_PENDING_COMMANDS = ("READY", "CLAIMED", "LAUNCH_STARTED", "PROCESS_STARTED")
_BUSY_PHASES = ("ANALYZING", "PREPARING")
_BUSY_ATTEMPTS = ("RUNNING", "SUBMITTING")


def _count(connection: sqlite3.Connection, query: str, values: tuple = ()) -> int:
    return int(connection.execute(query, values).fetchone()[0])


def check(db_path: Path, *, baseline_uncertain: int | None = None) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError("classic SQLite database is unavailable")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        expected = {"parent_task", "generation_attempt", "generation_command"}
        tables = {
            name for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not expected.issubset(tables):
            raise ValueError("classic production task schema is incomplete")
        pending_plans = _count(
            connection,
            "SELECT COUNT(*) FROM parent_task WHERE phase IN (?,?)",
            _BUSY_PHASES,
        )
        executing_plans = _count(
            connection,
            "SELECT COUNT(*) FROM parent_task "
            "WHERE phase='ANALYZING' AND analysis_stage IN "
            "('RUNNING','DURABLE_CLAIMED','PREPARING_IMAGES','PROVIDER_RUNNING','VALIDATING')",
        )
        live_attempts = _count(
            connection,
            "SELECT COUNT(*) FROM generation_attempt WHERE status IN (?,?)",
            _BUSY_ATTEMPTS,
        )
        paid_or_dispatch_pending = _count(
            connection,
            "SELECT COUNT(*) FROM generation_command "
            "WHERE status IN (?,?,?,?)",
            _PENDING_COMMANDS,
        )
        uncertain_commands = _count(
            connection,
            "SELECT COUNT(*) FROM generation_command WHERE status='UNCERTAIN'",
        )
        uncertain = _count(
            connection,
            "SELECT COUNT(*) FROM generation_attempt "
            "WHERE status='UNKNOWN_AFTER_SUBMIT'",
        )
        retry_delivery = _count(
            connection,
            "SELECT COUNT(*) FROM generation_attempt a "
            "JOIN generation_variant v ON v.id=a.variant_id "
            "WHERE a.status='SUCCEEDED' AND "
            "a.last_error_code='DOWNLOAD_RETRYABLE' AND "
            "v.download_status='FAILED'",
        )
        cut_active = 0
        cut_active_by_status: dict[str, int] = {}
        oldest_cut_update = None
        if "multi_cutflow_task" in tables:
            active_where = (
                "status IN ('QUEUED','READY','ACCEPTED','IMPORTING','EDITING','RUNNING','UNKNOWN')"
            )
            cut_active_by_status = {
                str(key): int(amount)
                for key, amount in connection.execute(
                    "SELECT status, COUNT(*) FROM multi_cutflow_task "
                    f"WHERE {active_where} GROUP BY status"
                )
            }
            cut_active = sum(cut_active_by_status.values())
            cut_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(multi_cutflow_task)")
            }
            if "updated_at" in cut_columns:
                oldest_cut_update = connection.execute(
                    "SELECT MIN(updated_at) FROM multi_cutflow_task "
                    f"WHERE {active_where}"
                ).fetchone()[0]
        durable_confirmation = 0
        durable_confirmation_review = 0
        if "outbox_event" in tables:
            durable_confirmation = _count(
                connection, "SELECT COUNT(*) FROM outbox_event "
                "WHERE event_type IN ('DURABLE_CONFIRMATION_REQUESTED',"
                "'DURABLE_CONFIRMATION_STARTED')",
            )
            durable_confirmation_review = _count(
                connection, "SELECT COUNT(*) FROM outbox_event "
                "WHERE event_type='DURABLE_CONFIRMATION_REVIEW'",
            )
        connection.rollback()
    blockers = []
    if pending_plans:
        blockers.append("PREPAID_PLANNING_IN_FLIGHT")
    if durable_confirmation:
        blockers.append("DURABLE_CONFIRMATION_IN_FLIGHT")
    if live_attempts:
        blockers.append("CLOUD_ATTEMPTS_IN_FLIGHT")
    if paid_or_dispatch_pending:
        blockers.append("PAID_COMMANDS_IN_FLIGHT")
    if cut_active:
        blockers.append("CUT_JOBS_IN_FLIGHT")
    if baseline_uncertain is not None and uncertain > baseline_uncertain:
        blockers.append("PAID_UNKNOWN_INCREASED")
    return {
        "checkedAtUtc": datetime.now(UTC).isoformat(),
        "mode": "read_only",
        "counts": {
            "activeOrQueuedPlanning": pending_plans,
            "currentlyPlanning": executing_plans,
            "activeCloudAttempts": live_attempts,
            "pendingPaidCommands": paid_or_dispatch_pending,
            "uncertainCommandsHistorical": uncertain_commands,
            "uncertainPaidHistorical": uncertain,
            "deliveryRetry": retry_delivery,
            "activeCutMulti": cut_active,
            "durableConfirmationInFlight": durable_confirmation,
            "durableConfirmationReview": durable_confirmation_review,
        },
        "baselineUnknown": baseline_uncertain,
        "quiescentForVersionSwitch": not blockers,
        "blockers": blockers,
        "cutActiveRequiresHandoffReview": cut_active > 0,
        "cutActiveByStatus": cut_active_by_status,
        "oldestActiveCutUpdatedAt": oldest_cut_update,
        "notes": (
            "Advisory only; historical UNKNOWN records are never cleared. "
            "Do not stop active Provider jobs, resubmit, or switch a live DB "
            "without a separate approved deploy and backup procedure."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline-unknown", type=int)
    args = parser.parse_args()
    if args.baseline_unknown is not None and args.baseline_unknown < 0:
        parser.error("--baseline-unknown must be non-negative")
    report = check(args.database, baseline_uncertain=args.baseline_unknown)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["quiescentForVersionSwitch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
