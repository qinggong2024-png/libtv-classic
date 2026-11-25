"""Isolated SQLite concurrency rehearsal for ALLWE classic's P0-2 proposal.

Never reads, copies, migrates or reconfigures the live production database.
Synthetic cloud receipts are local SQLite rows, NOT actual provider calls.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

_ALLOWED_MODES = ("delete", "wal")
_STAGES = ("upload", "cloud_accepted", "downloaded", "cut_receipt")


def _open(path: Path, *, timeout_seconds: float = 2.0) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=timeout_seconds)
    connection.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
    return connection


def _run_trial(directory: Path, *, mode: str, cloud_tasks: int, repeat: int) -> dict:
    if mode not in _ALLOWED_MODES:
        raise ValueError("only DELETE/WAL may be compared")
    if not 1 <= cloud_tasks <= 20:
        raise ValueError("cloud capacity must never exceed 20")
    path = directory / f"{mode}-{repeat}.db"
    if path.exists():
        raise FileExistsError("refusing to alter an existing SQLite database")
    init = _open(path)
    try:
        effective_mode = init.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
        if effective_mode.casefold() != mode:
            raise RuntimeError(f"SQLite refused journal mode {mode}: {effective_mode}")
        init.executescript(
            """
            CREATE TABLE simulated_paid_intent (
                task_id TEXT PRIMARY KEY, provider TEXT NOT NULL
            );
            CREATE TABLE stage_event (
                task_id TEXT NOT NULL, stage TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(task_id, stage)
            );
            CREATE TABLE simulated_cut_receipt (
                task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL
            );
            """
        )
        init.commit()
    finally:
        init.close()

    # Every operation has a unique durable identity. All work takes short
    # transactions; network requests, videos and actual payments are excluded.
    start = time.perf_counter()
    failures: list[str] = []
    retry_count = 0
    lock_count = 0
    lock = threading.Lock()

    def event(task_no: int, stage: str) -> None:
        nonlocal retry_count, lock_count
        if stage not in _STAGES:
            raise ValueError(stage)
        task_id = f"fake-task-{task_no:04d}"
        for attempt in range(5):
            connection = _open(path, timeout_seconds=0.5)
            try:
                connection.execute("BEGIN IMMEDIATE")
                if stage == "cloud_accepted":
                    connection.execute(
                        "INSERT OR IGNORE INTO simulated_paid_intent VALUES (?,?)",
                        (task_id, "fake-kling-no-provider"),
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO stage_event VALUES (?,?,?)",
                    (task_id, stage, "local-fixture"),
                )
                if stage == "cut_receipt":
                    connection.execute(
                        "INSERT INTO simulated_cut_receipt VALUES (?,1) "
                        "ON CONFLICT(task_id) DO UPDATE SET revision=MAX(revision,1)",
                        (task_id,),
                    )
                connection.commit()
                return
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if exc.sqlite_errorcode not in (
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
                ):
                    raise
                with lock:
                    lock_count += 1
                if attempt == 4:
                    raise
                with lock:
                    retry_count += 1
                time.sleep((attempt + 1) * 0.025)
            finally:
                connection.close()

    def worker(task_no: int) -> None:
        try:
            # Submitting twice must not count as paying twice.
            for stage in _STAGES:
                event(task_no, stage)
            event(task_no, "cloud_accepted")
            event(task_no, "cut_receipt")
        except Exception as exc:
            with lock:
                failures.append(f"{type(exc).__name__}: {exc}")

    def reader() -> None:
        try:
            connection = _open(path)
            try:
                for _ in range(30):
                    connection.execute(
                        "SELECT count(*) FROM stage_event"
                    ).fetchone()
                    time.sleep(0.001)
            finally:
                connection.close()
        except Exception as exc:
            with lock:
                failures.append(f"reader: {type(exc).__name__}: {exc}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        future_list = [
            pool.submit(worker, task) for task in range(cloud_tasks)
        ] + [pool.submit(reader) for _ in range(4)]
        for future in concurrent.futures.as_completed(future_list):
            future.result()

    check = _open(path)
    try:
        stage_count = check.execute(
            "SELECT COUNT(*) FROM stage_event"
        ).fetchone()[0]
        paid_count = check.execute(
            "SELECT COUNT(*) FROM simulated_paid_intent"
        ).fetchone()[0]
        cut_count = check.execute(
            "SELECT COUNT(*) FROM simulated_cut_receipt"
        ).fetchone()[0]
        bad_revision = check.execute(
            "SELECT COUNT(*) FROM simulated_cut_receipt WHERE revision != 1"
        ).fetchone()[0]
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    expected = cloud_tasks * len(_STAGES)
    return {
        "mode": mode,
        "cloud_tasks": cloud_tasks,
        "events": stage_count,
        "events_expected": expected,
        "simulated_unique_paid_intents": paid_count,
        "simulated_cut_receipts": cut_count,
        "invalid_cut_revisions": bad_revision,
        "integrity": integrity,
        "retries": retry_count,
        "busy_events": lock_count,
        "worker_errors": failures,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        "passed": (
            not failures and stage_count == expected and paid_count == cloud_tasks
            and cut_count == cloud_tasks and bad_revision == 0 and integrity == "ok"
        ),
    }


def run_rehearsal(cloud_tasks: int = 20, repeats: int = 3) -> list[dict]:
    if not 1 <= repeats <= 10:
        raise ValueError("repeats must be 1..10")
    with tempfile.TemporaryDirectory(prefix="allwe-classic-sqlite-isolated-") as temp:
        directory = Path(temp)
        return [
            _run_trial(directory, mode=mode, cloud_tasks=cloud_tasks, repeat=repeat)
            for mode in _ALLOWED_MODES
            for repeat in range(repeats)
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-tasks", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    results = run_rehearsal(args.cloud_tasks, args.repeats)
    print(json.dumps({
        "isolation": "temporary synthetic SQLite files only",
        "real_provider_calls": 0,
        "real_database_touched": False,
        "results": results,
    }, indent=2))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
