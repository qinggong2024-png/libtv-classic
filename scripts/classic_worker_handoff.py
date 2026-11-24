"""Read-only worker handoff preflight and explicit supervisor-restart hold.

A hold never stops a live worker. It only prevents the old supervisor from
starting a new runner/CUT after that worker exits. Release code must still
drain all leases before stopping workers, and must recheck status afterwards.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from classic_lan import STATE, STORAGE
from classic_v2_canary_preflight import check

MARKER = "ALLWE_CLASSIC_WORKER_HANDOFF_V1\n"
KINDS = ("runner", "cutflow")


def hold_file(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError("unsupported classic worker kind")
    return STORAGE / "runtime" / f"hold-{kind}-restart.flag"


def restart_held(kind: str) -> bool:
    path = hold_file(kind)
    try:
        # Unknown content also fails closed: an operator must review it before
        # the old supervisor creates a potentially duplicate worker.
        return path.is_file()
    except OSError:
        return True


def preflight(*, uncertain_baseline: int) -> dict:
    if not STATE.exists():
        raise RuntimeError("Classic instance state is missing")
    from classic_green_api import json_get, web_route

    route = web_route()
    blue_gate = json_get(route["activeApiPort"], "/api/v1/system/classic-new-video-admission")
    if (blue_gate.get("configured") is not True or
            blue_gate.get("newVideoAdmissionsPaused") is not True or
            blue_gate.get("deploymentId") != "libtv-classic-lan-0e3d1c8"):
        raise RuntimeError("Pause classic intake and verify the running gate first")
    report = check(STORAGE / "classic.db", baseline_uncertain=uncertain_baseline)
    if not report["quiescentForVersionSwitch"]:
        raise RuntimeError("Worker handoff blocked: original task/payment/CUT leases still active")
    return report


def _selected_kinds(kind: str | None) -> tuple[str, ...]:
    if kind is None:
        return KINDS
    if kind not in KINDS:
        raise ValueError("unsupported classic worker kind")
    return (kind,)


def arm_handoff(*, uncertain_baseline: int, kind: str | None = None) -> dict:
    report = preflight(uncertain_baseline=uncertain_baseline)
    created = []
    try:
        for selected in _selected_kinds(kind):
            path = hold_file(selected)
            if path.exists():
                if path.read_text(encoding="utf-8") != MARKER:
                    raise RuntimeError("An unknown worker hold marker exists")
                continue
            with path.open("x", encoding="utf-8") as handle:
                handle.write(MARKER)
            created.append(path)
        # A second read-only preflight catches tasks created concurrently
        # between the original test and writing the stop/restart hold.
        report = preflight(uncertain_baseline=uncertain_baseline)
        return report
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def disarm_handoff(kind: str | None = None) -> None:
    for selected in _selected_kinds(kind):
        path = hold_file(selected)
        if path.exists() and path.read_text(encoding="utf-8") != MARKER:
            raise RuntimeError("Refusing to remove an unrecognized worker hold")
    for selected in _selected_kinds(kind):
        hold_file(selected).unlink(missing_ok=True)


def plan_handoff(*, uncertain_baseline: int) -> dict:
    """Read-only handoff decision; never pauses admissions or starts workers."""
    from classic_stack import owned_process

    report = check(STORAGE / "classic.db", baseline_uncertain=uncertain_baseline)
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        processes = {
            item["name"]: bool(owned_process(item))
            for item in state.get("processes", [])
            if item.get("name") in KINDS
        }
    except (OSError, ValueError, TypeError):
        processes = {}
    blockers = list(report["blockers"])
    if set(processes) != set(KINDS) or not all(processes.values()):
        blockers.append("WORKER_IDENTITY_UNVERIFIED")
    return {
        "mode": "read-only-worker-handoff-plan",
        "workerProcessesAlive": processes,
        "restartHolds": {kind: restart_held(kind) for kind in KINDS},
        "taskCounts": report["counts"],
        "blockers": sorted(set(blockers)),
        "readyForShortIntakeFreezeAndExclusiveHandoff": not blockers,
        "nextAction": (
            "keep-existing-workers-and-reconcile-original-tasks" if blockers
            else "pause-new-intake-recheck-checkpoint-and-arm-selected-worker"
        ),
        "note": "No second Runner/CUT is started. Paid UNKNOWN records are not resubmitted.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "check", "arm", "disarm", "status"])
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--kind", choices=KINDS)
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    if args.action == "plan":
        decision = plan_handoff(uncertain_baseline=args.baseline_unknown)
        print(json.dumps(decision, indent=2))
        if not decision["readyForShortIntakeFreezeAndExclusiveHandoff"]:
            raise SystemExit(2)
    elif args.action == "status":
        print(json.dumps({kind: restart_held(kind) for kind in KINDS}))
    elif args.action == "check":
        print(json.dumps(preflight(uncertain_baseline=args.baseline_unknown), indent=2))
    elif args.action == "arm":
        if not args.apply:
            raise SystemExit("arm requires --apply (check is read-only)")
        print(json.dumps(arm_handoff(uncertain_baseline=args.baseline_unknown, kind=args.kind), indent=2))
    else:
        if not args.apply:
            raise SystemExit("disarm requires --apply")
        disarm_handoff(args.kind)
        print("Old supervisor restart holds removed; verify worker ownership immediately.")


if __name__ == "__main__":
    main()
