"""Read-only scheduled ALLWE classic director-memory metadata collection.

Intentionally independent of live API, Sol, Kling, CUT, and application jobs.
Windows Task Scheduler can invoke once every 5 minutes. Never passes a raw
product prompt or image into the durable collection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from libtv_agent.services.ootd_memory_shadow_collector import collect_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = collect_once(
        args.database, args.output, limit=args.limit, dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
