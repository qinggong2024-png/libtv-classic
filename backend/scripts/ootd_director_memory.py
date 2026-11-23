"""Manual, audited OOTD director memory maintenance; NEVER creates provider tasks.

Read or append only print-agnostic, reviewed reusable rules. The worker path is
strictly read-only and never auto-promotes a model's description to a fact.

PowerShell example:
  $env:PYTHONPATH='backend/src'
  python backend/scripts/ootd_director_memory.py --store var/director-memory.json list
  python backend/scripts/ootd_director_memory.py --store memory.json add-rule --file rule.json
  python backend/scripts/ootd_director_memory.py --store memory.json revoke --id rule.id
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from libtv_agent.services.ootd_director_memory import (
    MemoryRule,
    MemoryState,
    append_memory_revision,
    load_memory_rules,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="Read trusted knowledge file (no provider calls)")
    add = sub.add_parser("add-rule", help="Explicitly append one reviewed JSON rule")
    add.add_argument("--file", type=Path, required=True)
    revoke = sub.add_parser("revoke", help="Permanently retire a rule ID")
    revoke.add_argument("--id", required=True)
    args = parser.parse_args()

    if args.action == "list":
        rules = load_memory_rules(args.store)
        for rule in rules:
            print(
                f"{rule.rule_id} rev={rule.revision} state={rule.state.value} "
                f"scope={rule.brand}/{rule.destination.value}/{rule.garment_category or '*'}"
            )
        return 0
    if args.action == "add-rule":
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            parser.error("Rule input must be one JSON object")
        rule = MemoryRule.from_dict(payload)
        if rule.origin == "REVIEWED_VIDEO_OUTCOME" and not rule.reviewed_by:
            parser.error("Video outcome requires a named human reviewer")
    else:
        matches = [
            item for item in load_memory_rules(args.store)
            if item.rule_id == args.id
        ]
        if not matches:
            parser.error("Rule ID does not exist")
        newest = max(matches, key=lambda item: item.revision)
        rule = replace(
            newest, revision=newest.revision + 1, state=MemoryState.REVOKED
        )
    append_memory_revision(args.store, rule)
    print(
        f"appended {rule.rule_id} rev={rule.revision} "
        f"state={rule.state.value}; provider_calls=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
