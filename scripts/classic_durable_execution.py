"""Read-only Classic durable execution plan/status/rollback inspection.

No configuration write, route change, database mutation or process restart.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rollback_counts(database: Path) -> dict[str, int]:
    # The analysis execution stage changes after the claim. Count all active
    # analyses conservatively rather than missing an already-dispatched run.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        return {
            "activeAnalysis": db.execute(
                "SELECT COUNT(*) FROM parent_task WHERE phase='ANALYZING'"
            ).fetchone()[0],
            "confirmationInFlight": db.execute(
                "SELECT COUNT(*) FROM outbox_event WHERE event_type IN "
                "('DURABLE_CONFIRMATION_REQUESTED','DURABLE_CONFIRMATION_STARTED')"
            ).fetchone()[0],
            "confirmationReview": db.execute(
                "SELECT COUNT(*) FROM outbox_event WHERE event_type='DURABLE_CONFIRMATION_REVIEW'"
            ).fetchone()[0],
        }


def inspect(runtime: Path) -> dict:
    config = runpy.run_path(
        str(ROOT / "backend/src/libtv_agent/services/classic_durable_config.py")
    )
    path = runtime / config["NAME"]
    data = config["read_config"](path)
    counts = rollback_counts(runtime.parent / "classic.db")
    return {
        "configurationExists": path.is_file(),
        "configuration": data,
        "configRevision": config["revision"](data),
        "runnerReady": config["runner_ready"](runtime, data)
        if data["analysis"] or data["confirmation"]
        else None,
        "rollbackCounts": counts,
        "durableQueuesClear": not any(counts.values()),
        "note": "Read-only evidence, not authorization to disable or deploy.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    args = parser.parse_args()
    result = inspect(args.runtime)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["durableQueuesClear"] else 2)
