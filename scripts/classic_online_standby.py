"""Online preparation only: retire a proven-idle standby, never change routing.

The strict API activation and Worker handoff gates remain separate. This
operator path requires both installed APIs to share the current durable
execution and recovery contract, and an explicitly reviewed immutable
candidate that preserves startup, schema, dependencies and ownership code.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from classic_active_release_status import report as active_release_report
from classic_active_release_status import workspace_source_revision
from classic_api_drain import assess
from classic_green_api import DEPLOYMENT, json_get, validated_release, web_route
from classic_v2_canary_preflight import check

ACTIVE_WORK = {
    "PREPAID_PLANNING_IN_FLIGHT", "DURABLE_CONFIRMATION_IN_FLIGHT",
    "CLOUD_ATTEMPTS_IN_FLIGHT", "PAID_COMMANDS_IN_FLIGHT", "CUT_JOBS_IN_FLIGHT",
}
PROTECTED = (
    "backend/src/libtv_agent/services/classic_api_active_role.py",
    "backend/src/libtv_agent/services/classic_process_lock.py",
    "backend/src/libtv_agent/services/classic_durable_config.py",
    "backend/src/libtv_agent/runner/classic_checkpoint.py",
    "backend/src/libtv_agent/runner/classic_durable_readiness.py",
    "backend/src/libtv_agent/api/system.py",
    "backend/src/libtv_agent/config.py",
    "scripts/classic_lan.py",
)


def verify_candidate(root: Path, candidate: Path, active_revision: str, revision: str):
    from classic_worker_checkpoint import verify_worker_compatibility

    live = active_release_report(root)
    source = live.get("activeApi", {})
    if (not live.get("ok") or source.get("sourceRevision") != active_revision
            or source.get("diskMatchesRunning") is not True or not source.get("root")):
        raise RuntimeError("running API source identity is not verified")
    active_root = Path(source["root"]).resolve()
    if active_root.parent != root.resolve().parent:
        raise RuntimeError("active API source outside Classic workspace family")
    if workspace_source_revision(active_root) != active_revision:
        raise RuntimeError("active release source changed")
    validated_release(candidate, revision)
    # Exact candidate files must be committed. Staging is not a way to load
    # another session's dirty backend into the shared database process.
    status = subprocess.run(
        ["git", "-C", str(candidate), "status", "--porcelain", "--untracked-files=all",
         "--", "backend/src", "backend/alembic", "backend/alembic.ini", "pyproject.toml",
         "uv.lock", "scripts/classic_lan.py"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    if status.stdout.strip():
        raise RuntimeError("online standby candidate backend has uncommitted files")
    verify_worker_compatibility(active_root, candidate)
    for relative in PROTECTED:
        if (active_root / relative).read_bytes() != (candidate / relative).read_bytes():
            if (relative == "backend/src/libtv_agent/services/classic_durable_config.py"
                    and frozenset((active_revision, revision)) == RUNNER_COMPAT_API_PAIR):
                # Exact reviewed additive contract: both APIs still accept the
                # existing Runner; only a bounded verified checkpoint can queue.
                continue
            raise RuntimeError(f"online standby requires unchanged contract: {relative}")
    # The candidate may add middleware/routes, but not change startup/recovery
    # ownership. Compare ASTs rather than formatting or comments.
    relative = "backend/src/libtv_agent/app.py"
    names = {
        "lifespan", "_owned_recovery", "_analysis_recovery_loop",
        "_image_generation_recovery_loop", "_recover_local_jobs",
        "_recover_optional_jobs",
    }

    def functions(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names}

    before, after = functions(active_root / relative), functions(candidate / relative)
    if set(before) != names or before != after:
        raise RuntimeError("online standby requires unchanged API startup/recovery functions")


def assess_preparation(route, active, standby, active_gate, standby_gate,
                       active_durable, standby_durable, active_drain, standby_drain,
                       tasks, *, target, expected_active, expected_standby=None):
    expected_standby = expected_standby or expected_active
    blockers = []
    if target not in (8002, 8003):
        raise ValueError("invalid Classic target")
    current = 8003 if target == 8002 else 8002
    if (route.get("activeApiPort") != current or route.get("hotRouteVersion") != 1
            or route.get("configured") is not True
            or route.get("allowedApiPorts") != [8002, 8003]):
        blockers.append("ACTIVE_ROUTE_UNVERIFIED")
    if (active.get("sourceRevision") != expected_active
            or standby.get("sourceRevision") != expected_standby
            or not active.get("configRevision") or not active.get("schemaRevision")
            or active.get("configRevision") != standby.get("configRevision")
            or active.get("schemaRevision") != standby.get("schemaRevision")):
        blockers.append("INSTALLED_SOURCE_OR_CONTRACT_MISMATCH")
    for gate in (active_gate, standby_gate):
        if (gate.get("gateVersion") != 1 or gate.get("deploymentId") != DEPLOYMENT
                or gate.get("configured") is not True
                or gate.get("newVideoAdmissionsPaused") is not False):
            blockers.append("OPEN_INTAKE_IDENTITY_UNVERIFIED")
            break
    for durable in (active_durable, standby_durable):
        if (durable.get("contractVersion") != 1 or durable.get("deploymentId") != DEPLOYMENT
                or durable.get("analysis") != "READY" or durable.get("confirmation") != "READY"
                or not durable.get("configRevision")):
            blockers.append("DURABLE_EXECUTION_NOT_READY")
            break
    if active_durable.get("configRevision") != standby_durable.get("configRevision"):
        blockers.append("DURABLE_CONFIGURATION_MISMATCH")
    for drain, port, owns, revision in (
        (active_drain, current, True, expected_active),
        (standby_drain, target, False, expected_standby),
    ):
        if (drain.get("contractVersion") != 1 or drain.get("recoveryLockVersion") != 1
                or drain.get("deploymentId") != DEPLOYMENT or drain.get("port") != port
                or drain.get("sourceRevision") != revision
                or drain.get("ownsRecovery") is not owns
                or drain.get("durableAnalysis") is not True
                or drain.get("durableConfirmation") is not True):
            blockers.append("RECOVERY_OWNERSHIP_UNVERIFIED")
            break
    drain = assess(target, route, standby_drain, expected_revision=expected_standby)
    blockers.extend(drain["blockers"])
    task_blockers = tasks.get("blockers")
    if not isinstance(task_blockers, list):
        blockers.append("TASK_REPORT_UNVERIFIED")
        task_blockers = []
    blockers.extend(b for b in task_blockers if b not in ACTIVE_WORK)
    return {
        "mode": "online-standby-preparation-only",
        "activePort": current, "targetPort": target,
        "readyToPrepareStandby": not blockers,
        "blockers": sorted(set(blockers)),
        "informationalActiveWork": [b for b in task_blockers if b in ACTIVE_WORK],
        "taskCounts": tasks.get("counts", {}),
        "permitsRouteSwitch": False, "permitsWorkerHandoff": False,
    }


def inspect(root, candidate, revision, expected_active, target, baseline_unknown,
            *, expected_standby=None):
    from classic_blue_api import supervisor_supports_blue

    if not supervisor_supports_blue(root / "var/classic-lan/runtime"):
        raise RuntimeError("running supervisor does not support safe slot preparation")
    verify_candidate(root, candidate, expected_active, revision)
    current = 8003 if target == 8002 else 8002

    def read(port, name):
        return json_get(port, "/api/v1/system/" + name)

    route = web_route()
    active = read(current, "status")
    standby = read(target, "status")
    if expected_standby is not None:
        installed_slot_root(root, target, expected_standby)
    report = assess_preparation(
        route, active, standby, read(current, "classic-new-video-admission"),
        read(target, "classic-new-video-admission"),
        read(current, "classic-durable-execution"), read(target, "classic-durable-execution"),
        read(current, "classic-api-drain"), read(target, "classic-api-drain"),
        check(root / "var/classic-lan/classic.db", baseline_uncertain=baseline_unknown),
        target=target, expected_active=expected_active, expected_standby=expected_standby,
    )
    after = web_route()
    if (after.get("activeApiPort") != current or after.get("pendingRouteSelections") != 0
            or after.get("inFlightByPort", {}).get(str(target)) != 0):
        report["blockers"].append("STANDBY_ROUTE_CHANGED")
        report["readyToPrepareStandby"] = False
    return report, active, standby


def guard(root, candidate, revision, expected_active, target, baseline_unknown,
          *, expected_standby=None):
    report, active, standby = inspect(
        root, candidate, revision, expected_active, target, baseline_unknown,
        expected_standby=expected_standby,
    )
    if not report["readyToPrepareStandby"]:
        raise RuntimeError("online standby blocked: " + ", ".join(report["blockers"]))
    return active, standby


def verify_prepared(target, revision, original):
    current = 8003 if target == 8002 else 8002
    route = web_route()
    if route.get("activeApiPort") != current:
        raise RuntimeError("active route changed during standby preparation")
    active = json_get(current, "/api/v1/system/status")
    standby = json_get(target, "/api/v1/system/status")
    for key in ("sourceRevision", "configRevision", "schemaRevision"):
        if active.get(key) != original.get(key):
            raise RuntimeError("active source or contract changed")
    if (standby.get("sourceRevision") != revision
            or standby.get("configRevision") != original.get("configRevision")
            or standby.get("schemaRevision") != original.get("schemaRevision")):
        raise RuntimeError("prepared standby source or contract mismatch")
    json_get(target, "/api/v1/health/ready")
    durable = json_get(target, "/api/v1/system/classic-durable-execution")
    if durable.get("analysis") != "READY" or durable.get("confirmation") != "READY":
        raise RuntimeError("prepared standby durable execution not ready")
    from classic_api_drain import inspect as inspect_drain
    if not inspect_drain(target, revision)["ready"]:
        raise RuntimeError("prepared standby did not prove inactive/drained")


# This incident's reviewed API-only pair. Changes are limited to download
# metadata/diagnostics/health; the Runner, provider submission and image
# recovery implementations are byte-identical. Other pairs stay strict.
from classic_release_classifier import (
    API_ONLY_PAIR, ACTIVITY_API_ONLY_PAIR, IMAGE_API_ONLY_PAIR,
    RUNNER_COMPAT_API_PAIR, reviewed_pair,
)


def installed_slot_root(root, port, revision):
    from classic_blue_api import read_pin
    from classic_green_api import green_process, read_manifest
    from classic_lan import owned_process

    runtime = root / "var/classic-lan/runtime"
    if port == 8002:
        import json

        pin = read_pin(runtime, root)
        stack = json.loads((runtime / "stack.json").read_text(encoding="utf-8"))
        records = [r for r in stack["processes"] if r.get("name") == "api"]
        if (not pin or len(records) != 1 or not owned_process(records[0])
                or records[0].get("sourceRevision") != revision
                or records[0].get("releaseRoot") != pin.get("releaseRoot")):
            raise RuntimeError("blue standby process/pin not verified")
    elif port == 8003:
        pin = read_manifest()
        if not pin or not green_process(pin):
            raise RuntimeError("green standby process/pin not verified")
    else:
        raise ValueError("unknown Classic slot")
    if pin.get("sourceRevision") != revision:
        raise RuntimeError("installed standby source changed")
    return validated_release(Path(pin["releaseRoot"]), revision)


def verify_api_only_switch(root, *, current, target, expected_active, expected_target):
    if not reviewed_pair(expected_active, expected_target):
        raise RuntimeError("API-only switch requires the reviewed exact source pair")
    if web_route().get("activeApiPort") != current:
        raise RuntimeError("active route changed")
    candidate = installed_slot_root(root, target, expected_target)
    report, _, _ = inspect(root, candidate, expected_target, expected_active, target, 15,
                           expected_standby=expected_target)
    if not report["readyToPrepareStandby"]:
        raise RuntimeError("API-only switch refused: " + ", ".join(report["blockers"]))
    return report


if __name__ == "__main__":
    import argparse
    import json

    from classic_active_release_status import ROOT

    parser = argparse.ArgumentParser(description="Reviewed API-only switch preflight")
    parser.add_argument("--current", type=int, choices=(8002, 8003), required=True)
    parser.add_argument("--target", type=int, choices=(8002, 8003), required=True)
    parser.add_argument("--expected-active", required=True)
    parser.add_argument("--expected-target", required=True)
    args = parser.parse_args()
    if args.current == args.target:
        parser.error("distinct slots required")
    print(json.dumps(verify_api_only_switch(
        ROOT, current=args.current, target=args.target, expected_active=args.expected_active,
        expected_target=args.expected_target,
    )))
