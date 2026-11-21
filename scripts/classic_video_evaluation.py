"""Offline human video comparison. No providers, databases or generation gates.

Create a JSON worksheet with `init OUTPUT.json`, fill it after watching clips,
then use `summarize INPUT.json`. Never infer quality from prompts or mock results.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

DIMENSIONS = ("product_identity", "product_readability", "natural_motion", "useful_shots",
              "editable_footage")


def worksheet() -> dict:
    return {
        "version": "classic-video-evaluation-v1",
        "experiment": "填写唯一改动，例如商品图是否参与编排",
        "samples": [
            {
                "sampleId": f"same-input-{arm}", "inputGroup": "same-input",
                "arm": arm, "clipPath": "", "generationStatus": "pending",
                "promptRevision": "", "providerModel": "", "displayFocus": "overall",
                "productImagesAnalyzed": None, "durationSeconds": None,
                "costUsd": None, "waitSeconds": None, "reviewer": "",
                "reviewed": False, "usable": None, "usableSeconds": None,
                "dimensions": dict.fromkeys(DIMENSIONS),
                "observations": [],
            }
            for arm in ("baseline", "candidate")
        ],
    }


def _number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{field} must be a non-negative finite number or null")
    return float(value)


def summarize(data: dict) -> dict:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ids: set[str] = set()
    for row in data["samples"]:
        sample_id = row["sampleId"]
        if not isinstance(sample_id, str) or not sample_id or sample_id in ids:
            raise ValueError("sampleId must be nonempty and unique")
        ids.add(sample_id)
        if not row.get("inputGroup") or not row.get("arm"):
            raise ValueError("inputGroup and arm are required for comparable groups")
        if row.get("generationStatus") not in {"pending", "succeeded", "failed"}:
            raise ValueError("generationStatus must be pending, succeeded or failed")
        for field in ("durationSeconds", "costUsd", "waitSeconds", "usableSeconds"):
            _number(row.get(field), field)
        if row.get("reviewed") is True:
            if (row["generationStatus"] != "succeeded" or not row.get("clipPath")
                    or not row.get("reviewer") or not row.get("observations")
                    or type(row.get("usable")) is not bool):
                raise ValueError("Reviewed clips need a reviewer, observations and usable decision")
            if any(row.get("dimensions", {}).get(key) not in {"pass", "issue"}
                   for key in DIMENSIONS):
                raise ValueError("Reviewed clips need pass/issue for all five dimensions")
            seconds, duration = row.get("usableSeconds"), row.get("durationSeconds")
            if seconds is None or duration is None or seconds > duration:
                raise ValueError("Reviewed clips need usableSeconds between zero and duration")
        groups[(row["inputGroup"], row["arm"])].append(row)
    results = []
    for (input_group, arm), rows in sorted(groups.items()):
        reviewed = [row for row in rows if row.get("reviewed") is True]
        usable = sum(row["usable"] is True for row in reviewed)
        costs = [row.get("costUsd") for row in rows]
        complete_cost = all(cost is not None for cost in costs)
        all_resolved = all(row["generationStatus"] == "failed" or row in reviewed for row in rows)
        waits = [row["waitSeconds"] for row in rows if row.get("waitSeconds") is not None]
        results.append({
            "inputGroup": input_group, "arm": arm, "samples": len(rows),
            "failed": sum(row["generationStatus"] == "failed" for row in rows),
            "reviewed": len(reviewed), "usable": usable,
            "usableRateAmongReviewed": usable / len(reviewed) if reviewed else None,
            "usableRateAllAttempts": usable / len(rows) if all_resolved else None,
            "knownCostUsd": sum(cost for cost in costs if cost is not None),
            "missingCostCount": sum(cost is None for cost in costs),
            "costPerUsableClipUsd": sum(costs) / usable
            if complete_cost and all_resolved and usable else None,
            "meanKnownWaitSeconds": sum(waits) / len(waits) if waits else None,
            "knownWaitCount": len(waits),
            "issueCounts": {
                key: sum(row["dimensions"][key] == "issue" for row in reviewed)
                for key in DIMENSIONS
            },
        })
    return {
        "experiment": data.get("experiment", ""), "groups": results,
        "interpretation": "Manual descriptive comparison only; unreviewed is not a pass. "
                          "No statistical significance, automatic winner or resubmission inferred.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "summarize"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.action == "init":
        # Do not overwrite an operator's observations.
        with args.path.open("x", encoding="utf-8") as stream:
            json.dump(worksheet(), stream, ensure_ascii=False, indent=2)
        print(f"Created unreviewed worksheet: {args.path}")
    else:
        report = summarize(json.loads(args.path.read_text(encoding="utf-8-sig")))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
