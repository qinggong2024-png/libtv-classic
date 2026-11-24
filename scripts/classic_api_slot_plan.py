"""Symmetric, read-only API slot release planning for Classic 5176.

No process or route is changed. The plan proves which slot must be prepared,
pins source/config/schema and reports retirement evidence for the old slot.
The current supervisor's primary slot ownership is intentionally preserved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from classic_active_release_status import ROOT, get_json, workspace_source_revision
from classic_api_drain import assess as assess_drain
from classic_green_api import DEPLOYMENT
from classic_v2_canary_preflight import check as check_tasks


def make_plan(
    route: dict,
    active: dict,
    standby: dict | None,
    candidate_revision: str,
    gate: dict,
    task_report: dict,
    standby_drain: dict | None,
) -> dict:
    port = route.get("activeApiPort")
    if port not in (8002, 8003):
        raise RuntimeError("Classic active route is unknown")
    if len(candidate_revision) != 64 or any(
        c not in "0123456789abcdef" for c in candidate_revision
    ):
        raise ValueError("candidate must have a full source fingerprint")
    target = 8003 if port == 8002 else 8002
    blockers = list(task_report.get("blockers", ["TASK_PREFLIGHT_UNVERIFIED"]))
    if (
        route.get("hotRouteVersion") != 1
        or route.get("configured") is not True
        or route.get("allowedApiPorts") != [8002, 8003]
    ):
        blockers.append("CLASSIC_ROUTE_UNVERIFIED")
    if (
        gate.get("deploymentId") != DEPLOYMENT
        or gate.get("configured") is not True
        or gate.get("newVideoAdmissionsPaused") is not True
    ):
        blockers.append("STRICT_INTAKE_GATE_NOT_PAUSED")
    if not all(active.get(key) for key in ("sourceRevision", "configRevision", "schemaRevision")):
        blockers.append("ACTIVE_IDENTITY_UNVERIFIED")
    compatible = bool(
        standby
        and standby.get("sourceRevision") == candidate_revision
        and active.get("configRevision") == standby.get("configRevision")
        and active.get("schemaRevision") == standby.get("schemaRevision")
    )
    drain = None
    if standby and standby_drain:
        drain = assess_drain(
            target, route, standby_drain, expected_revision=standby.get("sourceRevision")
        )
    if not compatible:
        blockers.append("TARGET_SOURCE_OR_CONTRACT_NOT_READY")
        if standby and (not drain or not drain["ready"]):
            blockers.append("STANDBY_RETIREMENT_NOT_PROVEN")
    return {
        "mode": "read_only_strict_plan",
        "activePort": port,
        "targetPort": target,
        "activeRevision": active.get("sourceRevision"),
        "candidateRevision": candidate_revision,
        "configRevision": active.get("configRevision"),
        "schemaRevision": active.get("schemaRevision"),
        "targetAlreadyCompatible": compatible,
        "targetDrain": drain,
        "blockers": blockers,
        "readyForExistingStrictSwitch": not blockers,
        "taskCounts": task_report.get("counts", {}),
    }


def inspect(candidate: Path, *, root: Path = ROOT, baseline_unknown: int = 15) -> dict:
    candidate = candidate.resolve()
    if candidate == root.resolve() or candidate.parent != root.resolve().parent:
        raise ValueError("candidate must be a separate sibling release worktree")
    if (candidate / ".env").exists() or list((candidate / "frontend").glob(".env*")):
        raise ValueError("candidate must not include environment overrides")
    revision = workspace_source_revision(candidate)
    if not revision:
        raise ValueError("candidate source fingerprint unavailable")
    url = "http://127.0.0.1:5176"
    route = get_json(url + "/__classic_api_route/status") or {}
    port = route.get("activeApiPort")
    if port not in (8002, 8003):
        raise RuntimeError("Classic route unavailable")
    target = 8003 if port == 8002 else 8002
    active = get_json(f"http://127.0.0.1:{port}/api/v1/system/status") or {}
    standby = get_json(f"http://127.0.0.1:{target}/api/v1/system/status")
    drain = get_json(f"http://127.0.0.1:{target}/api/v1/system/classic-api-drain")
    gate = get_json(url + "/api/v1/system/classic-new-video-admission") or {}
    task_report = check_tasks(
        root / "var" / "classic-lan" / "classic.db", baseline_uncertain=baseline_unknown
    )
    return make_plan(route, active, standby, revision, gate, task_report, drain)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-unknown", type=int, default=15)
    args = parser.parse_args()
    report = inspect(args.candidate, baseline_unknown=args.baseline_unknown)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["readyForExistingStrictSwitch"] else 2)
