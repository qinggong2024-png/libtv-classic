"""One-shot / read-only-effect COORD receipt ingestion; no remote execution."""
from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAILBOX = runpy.run_path(str(ROOT / "backend/src/libtv_agent/services/cut_coord_mailbox.py"))
consume_once = MAILBOX["consume_once"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="Override handoff root (e.g. isolated fixture)")
    parser.add_argument("--storage", default=str(ROOT / "var" / "classic-lan"))
    args = parser.parse_args()
    if args.root:
        root = Path(args.root)
    else:
        module = runpy.run_path(str(ROOT / "scripts" / "classic_cutflow.py"))
        env = module["cutflow_environment"](Path(args.storage))
        if not env.get("CUTFLOW_HANDOFF_ROOT"):
            print("COORD_NOT_CONFIGURED")
            return 2
        root = Path(env["CUTFLOW_HANDOFF_ROOT"])
    try:
        print(json.dumps(consume_once(root), ensure_ascii=False))
    except (OSError, ValueError):
        print("COORD_READ_UNAVAILABLE")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
