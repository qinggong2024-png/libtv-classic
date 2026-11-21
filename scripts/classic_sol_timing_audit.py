"""Read-only Sol call timing and historical upstream/cloud queue audit.

Only aggregates timestamps and safe numeric outcomes. Never reads prompt content,
uploads, task IDs, provider credentials, or contacts an external API.
Historical occupancy is sampled from local timestamps, not provider history.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("first_request", "motion_review", "rewrite")
OUTCOMES = ("ok", "error", "timeout")
REVIEW_RESULTS = ("skipped", "different", "repeat", "failed")
REWRITE_RESULTS = ("not_needed", "attempted", "used", "invalid", "failed")
EVENTS = ("SOL_STAGE_TIMING", "SOL_VARIANT_RESULT")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def describe(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_seconds": None, "p90_seconds": None,
                "p95_seconds": None, "max_seconds": None}
    return {
        "count": len(ordered),
        "p50_seconds": round(statistics.median(ordered), 2),
        "p90_seconds": round(ordered[math.ceil(len(ordered) * 0.90) - 1], 2),
        "p95_seconds": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2),
        "max_seconds": round(ordered[-1], 2),
    }


def collect_logs(root: Path, *, cutoff: datetime, max_bytes: int) -> dict:
    samples: dict[str, list[float]] = {name: [] for name in STAGES}
    outcome_counts: dict[str, Counter] = {name: Counter() for name in STAGES}
    review_counts: Counter = Counter()
    rewrite_counts: Counter = Counter()
    included = Counter()
    searched = []
    truncated = []
    timestamps: list[datetime] = []
    for filename in ("runner.log", "api.log"):
        path = root / "var" / "classic-lan" / "logs" / filename
        if not path.is_file():
            continue
        searched.append(filename)
        size = path.stat().st_size
        with path.open("rb") as file:
            if size > max_bytes:
                truncated.append(filename)
                file.seek(size - max_bytes)
                file.readline()  # discard a potentially partial first line
            for raw in file:
                line = raw.decode("utf-8", errors="replace")
                for event in EVENTS:
                    marker = event + " "
                    position = line.find(marker)
                    if position < 0:
                        continue
                    try:
                        payload = json.loads(line[position + len(marker):].strip())
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    stamp = parse_time(payload.get("at_utc"))
                    if stamp is None or not cutoff <= stamp <= datetime.now(UTC) + timedelta(minutes=2):
                        continue
                    if event == "SOL_STAGE_TIMING":
                        stage = payload.get("stage")
                        outcome = payload.get("outcome")
                        amount = payload.get("elapsed_seconds")
                        if (stage not in STAGES or outcome not in OUTCOMES
                                or type(amount) not in (float, int)
                                or not 0 <= amount <= 86400):
                            continue
                        samples[stage].append(float(amount))
                        outcome_counts[stage][outcome] += 1
                    else:
                        review = payload.get("review_result")
                        rewrite = payload.get("rewrite_result")
                        if review not in REVIEW_RESULTS or rewrite not in REWRITE_RESULTS:
                            continue
                        review_counts[review] += 1
                        rewrite_counts[rewrite] += 1
                    included[event] += 1
                    timestamps.append(stamp)
                    break
    first_ok = outcome_counts["first_request"]["ok"]
    reviewed = sum(review_counts[r] for r in ("different", "repeat", "failed"))
    repeats = review_counts["repeat"]
    return {
        "data_source": "bounded local API/Runner log tails; new events only, not retroactive",
        "stages": {stage: {**describe(samples[stage]), "outcomes": dict(outcome_counts[stage])}
                   for stage in STAGES},
        "completed_variants": sum(review_counts.values()),
        "review_results": dict(review_counts),
        "rewrite_results": dict(rewrite_counts),
        "review_invocation_rate_of_completed_variants": (
            round(reviewed / sum(review_counts.values()), 3) if review_counts else None
        ),
        "repeat_rate_of_reviews_with_valid_decision": (
            round(repeats / (repeats + review_counts["different"]), 3)
            if repeats + review_counts["different"] else None
        ),
        "rewrite_adoption_rate": (
            round(rewrite_counts["used"] / sum(rewrite_counts[r]
                  for r in ("used", "invalid", "failed")), 3)
            if any(rewrite_counts[r] for r in ("used", "invalid", "failed")) else None
        ),
        "first_request_successes": first_ok,
        "log_coverage": {
            "scanned_files": searched,
            "tail_truncated_files": truncated,
            "max_bytes_per_log": max_bytes,
            "events_in_window": dict(included),
            "first_observed_utc": min(timestamps).isoformat() if timestamps else None,
            "complete_window_guaranteed": False,
        },
    }


def queue_pressure(db_path: Path, *, hours: int, cloud_limit: int,
                   interval_minutes: int = 5, now: datetime | None = None) -> dict:
    end = now or datetime.now(UTC)
    start = end - timedelta(hours=hours)
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        analysis = conn.execute(
            "SELECT created_at, analysis_started_at, analysis_heartbeat_at, "
            "analysis_stage, phase FROM parent_task "
            "WHERE workflow_kind='FIXED_TEMPLATE' "
            "AND julianday(created_at) <= julianday(?) "
            "AND (julianday(analysis_started_at) >= julianday(?) "
            "OR (analysis_started_at IS NULL AND analysis_stage = 'QUEUED' "
            "AND phase = 'ANALYZING') "
            "OR julianday(analysis_heartbeat_at) >= julianday(?))",
            (end.isoformat(), start.isoformat(), start.isoformat()),
        ).fetchall()
        cloud = conn.execute(
            "SELECT submitted_at, process_started_at, completed_at, status "
            "FROM generation_attempt "
            "WHERE provider='kling_official_api' AND task_id IS NOT NULL "
            "AND task_id != '' "
            "AND julianday(COALESCE(submitted_at, process_started_at)) <= julianday(?) "
            "AND (julianday(completed_at) >= julianday(?) OR (completed_at IS NULL "
            "AND status IN ('RUNNING','RECONCILING','UNKNOWN_AFTER_SUBMIT')))",
            (end.isoformat(), start.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    # Occupancy history is an approximation from persisted local acceptance and
    # terminal timestamps; it cannot reconstruct remote provider capacity exactly.
    task_windows = []
    for created, began, beat, stage, phase in analysis:
        a, b, c = parse_time(created), parse_time(began), parse_time(beat)
        if a is None:
            continue
        running_end = (end if stage == "RUNNING" and phase == "ANALYZING" else c)
        task_windows.append((a, b, running_end))
    cloud_windows = []
    for submitted, started, finished, status in cloud:
        a, b = parse_time(submitted or started), parse_time(finished)
        if a:
            cloud_windows.append((a, b or end))
    step = timedelta(minutes=interval_minutes)
    cursor = start
    occupancy = []
    queue = []
    working = []
    while cursor < end:
        occupancy.append(sum(a <= cursor < b for a, b in cloud_windows))
        queue.append(sum(a <= cursor and (b is None or cursor < b)
                         for a, b, _ in task_windows))
        working.append(sum(b is not None and b <= cursor
                           and (c is None or cursor < c)
                           for _, b, c in task_windows))
        cursor += step
    available = [used < cloud_limit for used in occupancy]
    upstream_backlog = [
        available[i] and (queue[i] > 0 or working[i] > 0)
        for i in range(len(occupancy))
    ]
    queued_idle = [
        available[i] and queue[i] > 0 for i in range(len(occupancy))
    ]
    return {
        "sampling_minutes": interval_minutes,
        "sampling_points": len(occupancy),
        "local_cloud_occupancy_max": max(occupancy, default=0),
        "local_cloud_occupancy_p90": (
            sorted(occupancy)[math.ceil(len(occupancy) * 0.9) - 1]
            if occupancy else None
        ),
        "analysis_queued_max": max(queue, default=0),
        "analysis_running_max": max(working, default=0),
        "samples_with_cloud_spare_slots_and_analysis_queued": sum(queued_idle),
        "samples_with_cloud_spare_slots_and_upstream_work": sum(upstream_backlog),
        "share_with_cloud_spare_slots_and_analysis_queued": (
            round(sum(queued_idle) / len(queued_idle), 3) if queued_idle else None
        ),
        "interpretation": (
            "Five-minute historical snapshots reconstructed from local DB timestamps, "
            "not actual continuous cloud/provider or Sol worker queue telemetry. "
            "A queued sample can coexist with idle cloud slots without proving "
            "Sol speed is the throughput constraint. Do not infer per-account occupancy."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--cloud-limit", type=int, default=20)
    parser.add_argument("--db", type=Path, default=ROOT / "var/classic-lan/classic.db")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--max-log-bytes", type=int, default=20_000_000)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168 or not 1 <= args.cloud_limit <= 100:
        parser.error("Hours must be 1..168 and cloud limit 1..100")
    if not 0 <= args.max_log_bytes <= 100_000_000:
        parser.error("max-log-bytes must be 0..100000000")
    if not args.db.is_file():
        parser.error("database does not exist")
    now = datetime.now(UTC)
    print(json.dumps({
        "snapshot_utc": now.isoformat(),
        "window_hours": args.hours,
        "scope": "classic_read_only_sol_calls_and_historical_capacity",
        "sol_calls": collect_logs(args.root, cutoff=now - timedelta(hours=args.hours),
                                  max_bytes=args.max_log_bytes),
        "historical_queue_pressure": queue_pressure(
            args.db, hours=args.hours, cloud_limit=args.cloud_limit, now=now,
        ),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
