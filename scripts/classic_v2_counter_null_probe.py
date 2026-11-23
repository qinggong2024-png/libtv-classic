"""Read-only aggregate check of the two historic nullable generation counters.

Only returns aggregate counts; never selects prompt, task, account or user data.
Does not mutate, checkpoint, vacuum, migrate or copy the production database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def run_probe(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError("SQLite database missing")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(CASE WHEN submission_attempt_count IS NULL THEN 1 ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN reconciliation_attempt_count IS NULL THEN 1 ELSE 0 END),0) "
            "FROM generation_attempt"
        ).fetchone()
        return {
            "scope": "read-only aggregate counts, no business row retrieval",
            "attempt_count": int(row[0]),
            "submission_counter_null": int(row[1]),
            "reconciliation_counter_null": int(row[2]),
            "data_modified": False,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_probe(args.database), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
