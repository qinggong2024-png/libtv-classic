"""Stage a reviewed Classic joint release without stopping production.

The plan distinguishes standby preparation, API activation, worker handoff
and frontend publication. It never claims that staging is activation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from classic_active_release_status import ROOT, git_head, read_json_file, workspace_source_revision
from classic_api_slot_plan import inspect as strict_plan
from classic_online_standby import inspect as standby_plan, reviewed_pair


def runner_transition(root, candidate_revision):
    config = read_json_file(root / "var/classic-lan/runtime/durable-execution.json")
    if not isinstance(config, dict) or config.get("version") != 1:
        return {"state": "CONFIG_UNVERIFIED", "readyForRunnerReplacement": False}
    if config.get("analysis") is not True or config.get("confirmation") is not True:
        return {"state": "DURABLE_CONTRACT_NOT_READY", "readyForRunnerReplacement": False}
    configured = config.get("runnerSourceRevision")
    old = configured
    runtime = root / "var/classic-lan/runtime"
    if (runtime / "worker-runner-pin.json").exists():
        try:
            from classic_worker_source import worker_source
            _, old = worker_source("runner", runtime, root)
        except (OSError, ValueError, RuntimeError):
            return {"state": "RUNNER_PIN_UNVERIFIED", "readyForRunnerReplacement": False}
    if not isinstance(old, str) or len(old) != 64:
        return {"state": "RUNNER_PIN_UNVERIFIED", "readyForRunnerReplacement": False}
    return {
        "state": ("MATCHING_RUNNER_PIN" if old == candidate_revision
                  else "RUNNER_PIN_TRANSITION_REQUIRED"),
        "readyForRunnerReplacement": old == candidate_revision,
        "currentRunnerSourceRevision": old,
        "candidateRunnerSourceRevision": candidate_revision,
        "configuredRunnerBaseline": configured,
        "note": "Effective validated Worker pin takes precedence over the original durable baseline; process adoption is checked separately.",
    }


def plan(candidate, expected_active, baseline_unknown=15, *, root=ROOT, expected_standby=None):
    strict = strict_plan(candidate, root=root, baseline_unknown=baseline_unknown)
    revision = workspace_source_revision(candidate)
    head = git_head(candidate)
    if not head or not revision or strict["activeRevision"] != expected_active:
        raise RuntimeError("candidate or active source identity changed")
    if strict["targetAlreadyCompatible"]:
        stage = {"mode": "already-prepared", "readyToPrepareStandby": False,
                 "blockers": [], "permitsRouteSwitch": False, "permitsWorkerHandoff": False}
    else:
        try:
            if expected_standby is None:
                raise RuntimeError("explicit expected standby revision required")
            stage, _, _ = standby_plan(root, candidate, revision, expected_active,
                                       strict["targetPort"], baseline_unknown,
                                       expected_standby=expected_standby)
            if (not reviewed_pair(expected_active, expected_standby)
                    or not reviewed_pair(expected_active, revision)):
                stage["blockers"].append("REVIEWED_EXACT_SOURCE_PAIR_REQUIRED")
                stage["readyToPrepareStandby"] = False
        except (OSError, RuntimeError, ValueError) as exc:
            stage = {"mode": "online-standby-preparation-only",
                     "readyToPrepareStandby": False, "blockers": [str(exc)],
                     "permitsRouteSwitch": False, "permitsWorkerHandoff": False}
    return {
        "mode": "staged-joint-release", "candidateRevision": revision,
        "candidateHead": head,
        "activeRevision": expected_active, "standbyPreparation": stage,
        "strictActivation": strict,
        "runnerConfigTransition": runner_transition(root, revision),
        "nextAction": ("prepare-inactive-api" if stage["readyToPrepareStandby"]
                       else "review-strict-activation-and-worker-handoff"),
        "frontendPublish": "after-matching-backend-contract",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-active", required=True)
    parser.add_argument("--expected-standby", required=True)
    parser.add_argument("--expected-candidate-revision")
    parser.add_argument("--expected-candidate-head")
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--prepare-standby", action="store_true",
                        help="Explicitly prepare standby only (default dry-run)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    if args.apply and not args.prepare_standby:
        parser.error("--apply is supported only with --prepare-standby")
    if args.apply and not (args.expected_candidate_head and args.expected_candidate_revision):
        parser.error("apply requires approved candidate Git SHA and source fingerprint")
    report = plan(args.candidate, args.expected_active, args.baseline_unknown,
                  expected_standby=args.expected_standby)
    print(json.dumps(report, indent=2))
    if args.prepare_standby:
        if ((args.expected_candidate_head
             and report["candidateHead"] != args.expected_candidate_head)
                or (args.expected_candidate_revision
                    and report["candidateRevision"] != args.expected_candidate_revision)):
            raise SystemExit("candidate identity changed")
        if not report["standbyPreparation"]["readyToPrepareStandby"]:
            raise SystemExit("standby preparation blocked")
        revision = report["candidateRevision"]
        target = report["strictActivation"]["targetPort"]
        if target == 8002:
            from classic_blue_api import replace_blue
            result = replace_blue(
                args.candidate, revision, expected_active=args.expected_active,
                baseline_unknown=args.baseline_unknown, apply=args.apply, online_prepare=True,
                expected_standby=args.expected_standby,
            )
        else:
            from classic_green_api import replace_green_online
            result = replace_green_online(
                args.candidate, revision, expected_active=args.expected_active,
                baseline_unknown=args.baseline_unknown, apply=args.apply,
                expected_standby=args.expected_standby,
            )
        print(json.dumps(result, indent=2))
