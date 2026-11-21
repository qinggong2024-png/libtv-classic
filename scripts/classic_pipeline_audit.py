"""Read-only performance baseline for the isolated ALLWE classic production line.

Never submits work, never queries a provider, never writes to the SQLite DB.
Only aggregate timings/statuses are emitted; user IDs, prompts and filenames are not.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sqlite3
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

UTC = dt.UTC
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def utc_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def elapsed(start: str | None, end: str | None) -> float | None:
    a, b = utc_time(start), utc_time(end)
    if not a or not b:
        return None
    seconds = (b - a).total_seconds()
    return seconds if 0 <= seconds <= 86400 * 7 else None


def summarize(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_seconds": None, "p90_seconds": None, "max_seconds": None}
    return {
        "count": len(ordered),
        "p50_seconds": round(statistics.median(ordered), 2),
        "p90_seconds": round(ordered[math.ceil(len(ordered) * 0.9) - 1], 2),
        "max_seconds": round(ordered[-1], 2),
    }


def fetch_count(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, args).fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def group_counts(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> dict[str, int]:
    return {str(key or "UNKNOWN"): int(count) for key, count in conn.execute(sql, args)}


def database_report(path: Path, hours: int, cloud_limit: int) -> dict[str, Any]:
    cutoff = (dt.datetime.now(UTC) - dt.timedelta(hours=hours)).replace(tzinfo=None)
    cutoff_str = cutoff.isoformat(sep=" ", timespec="seconds")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        samples: dict[str, list[float]] = {
            "upload_client_reported": [],
            "analysis_queue_wait": [],
            "analysis_execution": [],
            "attempt_to_provider_acceptance": [],
            "provider_acceptance_to_terminal": [],
            "provider_terminal_to_local_artifact": [],
        }
        task_rows = conn.execute(
            "SELECT created_at, analysis_started_at, analysis_heartbeat_at, "
            "analysis_stage, source_upload_duration_seconds "
            "FROM parent_task WHERE workflow_kind='FIXED_TEMPLATE' "
            "AND created_at>=?",
            (cutoff_str,),
        )
        for created, started, heartbeat, stage, upload in task_rows:
            if upload is not None and float(upload) > 0:
                samples["upload_client_reported"].append(float(upload))
            queue_wait = elapsed(created, started)
            if queue_wait is not None:
                samples["analysis_queue_wait"].append(queue_wait)
            if stage in ("COMPLETED", "FAILED"):
                analysis_time = elapsed(started, heartbeat)
                if analysis_time is not None:
                    samples["analysis_execution"].append(analysis_time)

        attempts = conn.execute(
            "SELECT created_at, submitted_at, completed_at "
            "FROM generation_attempt WHERE provider='kling_official_api' "
            "AND created_at>=?",
            (cutoff_str,),
        )
        for created, submitted, completed in attempts:
            dispatch_wait = elapsed(created, submitted)
            if dispatch_wait is not None:
                samples["attempt_to_provider_acceptance"].append(dispatch_wait)
            cloud_time = elapsed(submitted, completed)
            if cloud_time is not None:
                samples["provider_acceptance_to_terminal"].append(cloud_time)

        artifacts = conn.execute(
            "SELECT a.completed_at, d.created_at FROM download_artifact d "
            "JOIN generation_attempt a ON a.id=d.attempt_id "
            "WHERE a.provider='kling_official_api' AND d.status='READY' "
            "AND a.completed_at>=?",
            (cutoff_str,),
        )
        for terminal, ready in artifacts:
            processing_time = elapsed(terminal, ready)
            if processing_time is not None:
                samples["provider_terminal_to_local_artifact"].append(processing_time)

        cloud_active = fetch_count(
            conn,
            "SELECT count(*) FROM generation_attempt WHERE "
            "provider='kling_official_api' AND task_id IS NOT NULL AND task_id != '' "
            "AND status IN ('RUNNING','RECONCILING','UNKNOWN_AFTER_SUBMIT')",
        )
        local_ready = fetch_count(
            conn,
            "SELECT count(*) FROM generation_command cmd "
            "JOIN generation_attempt a ON a.id=cmd.attempt_id "
            "WHERE a.provider='kling_official_api' AND cmd.status='READY'",
        )
        prepared_blocked = group_counts(
            conn,
            "SELECT COALESCE(p.phase_reason_code,p.phase), count(*) "
            "FROM generation_attempt a "
            "JOIN generation_variant v ON v.id=a.variant_id "
            "JOIN parent_task p ON p.id=v.parent_task_id "
            "WHERE a.provider='kling_official_api' AND a.status='PREPARED' "
            "GROUP BY COALESCE(p.phase_reason_code,p.phase)",
        )
        unknown_with_handle = fetch_count(
            conn,
            "SELECT count(*) FROM generation_attempt WHERE "
            "provider='kling_official_api' AND status='UNKNOWN_AFTER_SUBMIT' "
            "AND task_id IS NOT NULL AND task_id != ''",
        )
        local_submissions_in_flight = fetch_count(
            conn,
            "SELECT count(*) FROM generation_submission_attempt WHERE finished_at IS NULL",
        )
        cut_statuses = group_counts(
            conn,
            "SELECT status,count(*) FROM multi_cutflow_task GROUP BY status",
        )
        cut_failures = group_counts(
            conn,
            "SELECT COALESCE(error_code,'UNKNOWN'),count(*) "
            "FROM multi_cutflow_task WHERE status='FAILED' AND created_at>=? "
            "GROUP BY COALESCE(error_code,'UNKNOWN')",
            (cutoff_str,),
        )
        return {
            "database_mb": round(path.stat().st_size / (1024 * 1024), 2),
            "sqlite_journal_mode": journal_mode,
            "cloud_limit_configured": cloud_limit,
            "cloud_active_local_records": cloud_active,
            "cloud_slots_remaining_by_local_records": max(0, cloud_limit - cloud_active),
            "local_ready_to_dispatch": local_ready,
            "prepared_budget_blocked_by_reason": prepared_blocked,
            "unknown_with_cloud_id": unknown_with_handle,
            "local_submission_records_in_flight": local_submissions_in_flight,
            "cut_multi_statuses_all_time": cut_statuses,
            "cut_failures_in_window": cut_failures,
            "stage_timings": {key: summarize(values) for key, values in samples.items()},
        }
    finally:
        conn.close()


def read_log_tail(path: Path, max_bytes: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - max_bytes))
        return stream.read().decode("utf-8", errors="replace")


def logs_report(root: Path, max_bytes: int) -> dict[str, Any]:
    logs = root / "var" / "classic-lan" / "logs"
    api = read_log_tail(logs / "api.log", max_bytes)
    runner = read_log_tail(logs / "runner-events.log", max_bytes)
    api_codes = Counter()
    api_routes = Counter()
    for match in re.finditer(r'"(GET|POST|PUT|PATCH|DELETE) (\S+) HTTP/1\.1" (\d{3})', api):
        method, path, status = match.groups()
        api_codes[status] += 1
        route = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/:id", path.split("?")[0])
        api_routes[f"{method} {route} {status}"] += 1
    lock_marker = "sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked"
    return {
        "tail_bytes_per_log": max_bytes,
        "request_statuses_in_api_tail": dict(api_codes),
        "most_requested_routes_in_api_tail": dict(api_routes.most_common(8)),
        "sqlite_lock_errors_in_api_tail": api.count(lock_marker),
        "sqlite_lock_errors_in_runner_tail": runner.count(lock_marker),
        "note": "Log-tail counts are samples, NOT rates or events within --hours.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "var/classic-lan/classic.db")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--cloud-limit", type=int, default=20)
    parser.add_argument("--api-log-tail-bytes", type=int, default=2_000_000)
    args = parser.parse_args()
    if not 1 <= args.hours <= 24 * 90:
        parser.error("--hours must be from 1 to 2160")
    if not 1 <= args.cloud_limit <= 1000:
        parser.error("--cloud-limit must be positive")
    if not 0 <= args.api_log_tail_bytes <= 20_000_000:
        parser.error("--api-log-tail-bytes must be 0..20000000")
    if not args.db.is_file():
        parser.error(f"database does not exist: {args.db}")
    report = {
        "snapshot_utc": dt.datetime.now(UTC).isoformat(),
        "window_hours": args.hours,
        "scope": "allwe_classic_read_only",
        "database": database_report(args.db, args.hours, args.cloud_limit),
        "logs": logs_report(PROJECT_ROOT, args.api_log_tail_bytes),
        "interpretation": {
            "upload_client_reported": "Client-reported upload, not server-side end-to-end.",
            "analysis_execution": "Image prep, Sol calls, comparisons and persistence.",
            "attempt_to_provider_acceptance": "Pre-submit elapsed, not a pure FIFO queue measure.",
            "provider_acceptance_to_terminal": "Includes provider processing and status detection.",
            "provider_terminal_to_local_artifact": "Includes download, validation and saving.",
            "cut_multi_statuses_all_time": "PENDING_REVIEW with outputs is not queued editing.",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
