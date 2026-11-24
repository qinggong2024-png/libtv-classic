"""Read-only retirement evidence for either Classic API slot.

The old web server/API must advertise the new drain contract. Missing proof
blocks retirement. This tool never changes routes, stops processes, or calls
a provider. It does not replace the existing strict release gate.
"""

from __future__ import annotations

import argparse
import json

from classic_green_api import DEPLOYMENT, json_get, web_route


def assess(port: int, route: dict, process: dict, *, expected_revision: str) -> dict:
    if port not in (8002, 8003):
        raise ValueError("only Classic API slots are supported")
    blockers = []
    if route.get("activeApiPort") not in (8002, 8003) or route.get("activeApiPort") == port:
        blockers.append("SLOT_IS_ACTIVE_OR_UNVERIFIED")
    if route.get("drainContractVersion") != 1 or route.get("pendingRouteSelections") != 0:
        blockers.append("PROXY_DRAIN_UNVERIFIED")
    if route.get("inFlightByPort", {}).get(str(port)) != 0:
        blockers.append("PROXY_REQUESTS_IN_FLIGHT")
    if (
        process.get("contractVersion") != 1
        or process.get("deploymentId") != DEPLOYMENT
        or process.get("port") != port
        or process.get("sourceRevision") != expected_revision
    ):
        blockers.append("API_PROCESS_IDENTITY_UNVERIFIED")
    required = {
        "analysis_jobs",
        "confirmation_jobs",
        "subject_element_jobs",
        "feedback_learning_jobs",
        "http",
        "recovery",
        "imageRefresh",
        "login",
    }
    counts = process.get("workCounts")
    if (
        not isinstance(counts, dict)
        or not required.issubset(counts)
        or any(type(count) is not int or count != 0 for count in counts.values())
        or process.get("ownsRecovery") is not False
        or process.get("retirementReady") is not True
    ):
        blockers.append("API_LOCAL_WORK_NOT_DRAINED")
    return {"port": port, "ready": not blockers, "blockers": blockers, "workCounts": counts}


def inspect(port: int, expected_revision: str) -> dict:
    if len(expected_revision) != 64 or any(c not in "0123456789abcdef" for c in expected_revision):
        raise ValueError("expected exact API source fingerprint")
    before = web_route()
    process = json_get(port, "/api/v1/system/classic-api-drain")
    after = web_route()
    report = assess(port, after, process, expected_revision=expected_revision)
    if (
        before.get("activeApiPort") != after.get("activeApiPort")
        or before.get("pendingRouteSelections") != 0
        or before.get("inFlightByPort", {}).get(str(port)) != 0
    ):
        report["blockers"].append("ROUTE_CHANGED_DURING_CHECK")
        report["ready"] = False
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, choices=(8002, 8003), required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    result = inspect(args.port, args.expected_revision)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready"] else 2)
