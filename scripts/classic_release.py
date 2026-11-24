"""Single, deliberately small Classic 5176 release entry.

Status/check are read-only. Frontend publish is explicit and reuses the tested
atomic publisher. Backend blue/green switching keeps its existing independent
paid-task handoff procedure; this tool never restarts workers or posts to CUT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from classic_active_release_status import frontend_release_ready, report, verify_release
from classic_v2_canary_preflight import check as check_tasks
from classic_release_classifier import committed_changes
from classic_isolated_frontend import (assess_isolated, prepare_isolated, publish_isolated,
                                      record_receipt, receipt_for, http_digest, frontend_scope,
                                      live_frontend_baseline, recover_frontend_receipt, tree)

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PAID_UNKNOWN = 15


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, timeout=15,
    ).stdout.strip()


def assess(root: Path, expected_main: str | None = None,
           expected_api: str | None = None, *, require_paused: bool = False,
           baseline_unknown: int = BASELINE_PAID_UNKNOWN,
           frontend_only: bool = False) -> dict:
    """Never write state or infer a release from a Git commit alone."""
    state = report(root)
    blockers: list[str] = []
    try:
        tasks = check_tasks(root / "var" / "classic-lan" / "classic.db",
                            baseline_uncertain=baseline_unknown)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        tasks = {"blockers": ["TASK_PREFLIGHT_UNAVAILABLE"], "error": type(error).__name__}
    if not frontend_only:
        blockers.extend(tasks["blockers"])
    try:
        head = git(root, "rev-parse", "HEAD")
        if git(root, "branch", "--show-current") != "main":
            blockers.append("NOT_PRODUCTION_MAIN")
        # Git on Windows can report an index metadata-only M after TypeScript
        # emits identical CRLF-normalized files. Inspect actual content + new
        # files rather than treating a false stat mismatch as a changed source.
        if git(root, "status", "--porcelain") and (
            git(root, "diff", "--numstat", "HEAD", "--")
            or git(root, "ls-files", "--others", "--exclude-standard")
        ):
            blockers.append("PRODUCTION_MAIN_DIRTY")
        if expected_main is not None and head != expected_main:
            blockers.append("MAIN_COMMIT_CHANGED")
    except (OSError, subprocess.SubprocessError):
        head = None
        blockers.append("GIT_STATUS_UNAVAILABLE")
    if not state.get("ok"):
        blockers.append("LIVE_CLASSIC_UNHEALTHY")
    if (state.get("mainCheckout") or {}).get("gitHead") != head:
        blockers.append("LIVE_MAIN_NOT_THIS_CHECKOUT")
    if (state.get("mainCheckout") or {}).get("matchesRunning") is not True:
        blockers.append("BACKEND_MAIN_NOT_ACTIVE")
    if (state.get("activeApi") or {}).get("diskMatchesRunning") is not True:
        blockers.append("BACKEND_DISK_NOT_ACTIVE")
    if expected_api is not None and (state.get("activeApi") or {}).get("sourceRevision") != expected_api:
        blockers.append("API_REVISION_CHANGED")
    if require_paused and state.get("admissionPaused") is not True:
        blockers.append("NEW_TASK_INTAKE_NOT_PAUSED")
    if state.get("activeApiPort") not in (8002, 8003):
        blockers.append("INVALID_CLASSIC_ROUTE")
    if frontend_only:
        if not state.get("activeConfigRevision") or not state.get("activeSchemaRevision"):
            blockers.append("LIVE_CONFIG_OR_SCHEMA_UNVERIFIED")
        if not isinstance(state.get("admissionPaused"), bool):
            blockers.append("ADMISSION_STATUS_UNVERIFIED")
        if not state.get("frontendIndexSha256"):
            blockers.append("FRONTEND_BASELINE_UNVERIFIED")
    return {
        "mainHead": head,
        "activeApiPort": state.get("activeApiPort"),
        "activeApiRevision": (state.get("activeApi") or {}).get("sourceRevision"),
        "frontendIndexSha256": state.get("frontendIndexSha256"),
        "admissionPaused": state.get("admissionPaused"),
        "taskCounts": tasks.get("counts", {}),
        "taskWarnings": tasks["blockers"] if frontend_only else [],
        "releaseMode": "frontend-only" if frontend_only else "strict",
        "activeConfigRevision": state.get("activeConfigRevision"),
        "activeSchemaRevision": state.get("activeSchemaRevision"),
        "blockers": sorted(set(blockers)),
        "ready": not blockers,
    }


@contextmanager
def release_lock(root: Path):
    lock = root / "var" / "classic-lan" / "runtime" / "classic-release.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("Another release lock exists; inspect it, never auto-delete") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(f"pid={os.getpid()} at={datetime.now(UTC).isoformat()}\n")
        yield
    finally:
        lock.unlink(missing_ok=True)


class ActivationBusy(RuntimeError):
    """No production mutation has started."""


@contextmanager
def route_activation_lock(runtime: Path):
    # Interoperates with classic_blue_api and the Node route operator using
    # their exact exclusive-create lock file, without importing psutil.
    path = runtime / "api-routing.lock"
    with path.open("x", encoding="ascii") as handle:
        try:
            handle.write(f"release-manager pid={os.getpid()}")
            handle.flush()
            yield
        finally:
            handle.close()
            path.unlink(missing_ok=True)


@contextmanager
def activation_lock(root: Path):
    # All supported frontend activations share the existing API/worker route lock.
    with ExitStack() as stack:
        try:
            stack.enter_context(release_lock(root))
            stack.enter_context(route_activation_lock(root / "var/classic-lan/runtime"))
        except (FileExistsError, RuntimeError) as error:
            raise ActivationBusy("Another production release owns the activation lock") from error
        yield


def publish_frontend(root: Path, expected_main: str, expected_api: str,
                     baseline_unknown: int, *, frontend_only: bool = False,
                     expected_old_frontend: str | None = None,
                     change_base: str | None = None) -> dict:
    """Strict by default; explicit frontend-only mode never pauses production."""
    with activation_lock(root):
        before = assess(root, expected_main, expected_api, require_paused=not frontend_only,
                        baseline_unknown=baseline_unknown, frontend_only=frontend_only)
        if frontend_only:
            try:
                changes = committed_changes(root, change_base) if change_base else None
            except ValueError:
                changes = None
            if not changes or not changes["frontendHotEligible"] or changes["target"] != expected_main:
                before["blockers"].append("FRONTEND_CHANGE_SCOPE_NOT_VERIFIED")
                before["ready"] = False
        if frontend_only and (not expected_old_frontend or before["frontendIndexSha256"] != expected_old_frontend):
            before["blockers"].append("FRONTEND_BASELINE_CHANGED")
            before["ready"] = False
        if not before["ready"]:
            return before
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        node = shutil.which("node")
        if not npm or not node:
            raise RuntimeError("Node and npm are required for the frontend build")
        subprocess.run([npm, "run", "build"], cwd=root / "frontend",
                       check=True, timeout=240)
        # A build is staging only. Recheck for tasks, concurrent commits and
        # backend cutovers immediately before the HTML pointer is changed.
        before_publish = assess(root, expected_main, expected_api,
                                require_paused=not frontend_only, baseline_unknown=baseline_unknown,
                                frontend_only=frontend_only)
        if frontend_only:
            try:
                current_changes = committed_changes(root, change_base) if change_base else None
            except ValueError:
                current_changes = None
            if not current_changes or not current_changes["frontendHotEligible"] or current_changes["target"] != expected_main:
                before_publish["blockers"].append("FRONTEND_CHANGE_SCOPE_CHANGED")
                before_publish["ready"] = False
            for key in ("activeApiPort", "activeConfigRevision", "activeSchemaRevision", "admissionPaused", "frontendIndexSha256"):
                if before_publish[key] != before[key]:
                    before_publish["blockers"].append("LIVE_BASELINE_CHANGED:" + key)
                    before_publish["ready"] = False
        if not before_publish["ready"]:
            return before_publish
        # Freeze the expected artifact before switching the entry, never infer
        # success from whatever happens to be on disk after publishing.
        staged_index = root / "frontend" / "dist-build" / "index.html"
        expected_sha = hashlib.sha256(staged_index.read_bytes()).hexdigest()
        old_index = root / "frontend" / "dist" / "index.html"
        archive = root / "var" / "classic-lan" / "runtime" / "release-history"
        archive.mkdir(parents=True, exist_ok=True)
        backup = archive / f"frontend-index-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.html"
        shutil.copy2(old_index, backup)
        subprocess.run([node, str(root / "scripts" / "publish_classic_frontend.mjs"), "--apply"],
                       cwd=root, check=True, timeout=120)
        new_index = (root / "frontend" / "dist" / "index.html").read_bytes()
        disk_sha = hashlib.sha256(new_index).hexdigest()
        with build_opener(ProxyHandler({})).open("http://127.0.0.1:5176/", timeout=5) as response:
            served_sha = hashlib.sha256(response.read()).hexdigest()
        result = assess(root, expected_main, expected_api,
                        require_paused=not frontend_only, baseline_unknown=baseline_unknown,
                        frontend_only=frontend_only)
        if frontend_only:
            for key in ("activeApiPort", "activeConfigRevision", "activeSchemaRevision", "admissionPaused"):
                if result[key] != before[key]:
                    result["blockers"].append("LIVE_BASELINE_CHANGED:" + key)
                    result["ready"] = False
        result.update(frontendPublished=True, expectedFrontendSha256=expected_sha,
                      servedFrontendSha256=served_sha, rollbackIndex=str(backup))
        if expected_sha != served_sha or expected_sha != disk_sha:
            result["blockers"].append("FRONTEND_HTTP_MISMATCH")
            result["ready"] = False
        if result["ready"]:
            result["receipt"] = record_receipt(root, {
                "mode": result["releaseMode"], "candidateHead": expected_main,
                "artifactSourceHead": expected_main, "frontendOld": before["frontendIndexSha256"],
                "frontendNew": expected_sha, "servedFrontendSha256": served_sha,
                "activeApiRevision": expected_api, "activeApiPort": result["activeApiPort"],
                "activeConfigRevision": result["activeConfigRevision"], "activeSchemaRevision": result["activeSchemaRevision"],
                "rollbackIndex": str(backup),
            })
        return result


def plan_release(root: Path, *, base: str) -> dict:
    """Read-only deploy decision; paid/CUT work is not mistaken for website downtime."""
    changes = committed_changes(root, base)
    front = changes["frontendHotEligible"]
    live = assess(root, frontend_only=front)
    reasons = list(live["blockers"])
    if live["mainHead"] != changes["target"]:
        reasons.append("CANDIDATE_IS_NOT_MAIN")
    if changes["mode"] == "no-runtime-change":
        action = "no-deploy-needed"
    elif front:
        action = ("prepare-isolated-frontend-candidate" if "BACKEND_MAIN_NOT_ACTIVE" in reasons
                  else "frontend-hot-publish-after-diff-and-api-review")
    elif changes["mode"] == "api-canary-review":
        action = "api-compatibility-review-and-standby-provenance-required"
    elif changes["mode"] == "worker-handoff-required":
        action = "worker-task-lease-handoff-required"
    else:
        action = "maintenance-or-manual-review-required"
    if not front and changes["mode"] != "no-runtime-change":
        reasons.append("NOT_AUTOMATICALLY_HOT_DEPLOYABLE")
    projected = frontend_scope(root, base, changes["target"]) if "paths" in changes and not front and changes.get("base") else None
    return {
        "changes": changes,
        "isolatedFrontendScope": projected,
        "isolatedFrontendNextAction": "prepare-isolated" if projected and projected["frontendHotEligible"] else None,
        "online": live,
        "nextAction": action,
        "canHotPublishFrontend": front and not reasons,
        "blockers": sorted(set(reasons)),
    }


def plan_live_frontend(root: Path, approved=None) -> dict:
    """Read-only discovery of exact pins for the next reviewed frontend release."""
    state = report(root)
    active = state.get("activeApi") or {}
    main = (state.get("mainCheckout") or {}).get("gitHead")
    result = {
        "releaseMode": "frontend-live-plan", "mainHead": main,
        "activeApiHead": active.get("gitHead"), "activeApiPort": state.get("activeApiPort"),
        "activeApiRevision": active.get("sourceRevision"),
        "frontendIndexSha256": state.get("frontendIndexSha256"),
        "fullMainApiMatches": (state.get("mainCheckout") or {}).get("matchesRunning") is True,
        "liveHealthy": state.get("ok") is True,
        "frontendReady": frontend_release_ready(state), "warnings": state.get("frontendWarnings", []),
        "ready": False, "blockers": [],
        "nextAction": "inspect-live-release-before-retry",
    }
    try:
        if not frontend_release_ready(state) or active.get("diskMatchesRunning") is not True:
            result["blockers"].append("LIVE_CLASSIC_UNHEALTHY_OR_API_DISK_DRIFT")
        elif state.get("admissionPaused") is not False:
            result["blockers"].append("ADMISSION_NOT_OPEN")
        elif (git(root, "branch", "--show-current") != "main"
              or git(root, "rev-parse", "HEAD") != main
              or git(root, "diff", "--numstat", "HEAD", "--")
              or git(root, "ls-files", "--others", "--exclude-standard")):
            result["blockers"].append("PRODUCTION_MAIN_CHANGED_OR_DIRTY")
        else:
            baseline = live_frontend_baseline(root, state)
            if not baseline:
                baseline = recover_frontend_receipt(root, state)
            if not baseline:
                result["blockers"].append("LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED")
            else:
                result["baseline"] = baseline
                result["receiptRecoveryRequired"] = baseline.get("recoveryRequired", False)
                base = baseline["base"]
                approved = approved or main
                if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{40}", approved):
                    raise ValueError("FULL_APPROVED_FRONTEND_HEAD_REQUIRED")
                git(root, "merge-base", "--is-ancestor", approved, main)
                git(root, "merge-base", "--is-ancestor", base, approved)
                result["approvedFrontendHead"] = approved
                if tree(root, base) == tree(root, approved):
                    result.update(ready=True, nextAction="frontend-already-current")
                else:
                    scope = frontend_scope(root, base, approved)
                    result["changes"] = scope
                    if scope["frontendHotEligible"]:
                        result.update(ready=True, nextAction="review-api-compatibility-and-prepare-isolated",
                            prepareArgs=["--change-base", base, "--approved-frontend-head", approved,
                                "--expected-main-head", main, "--expected-api-revision", active["sourceRevision"],
                                "--expected-old-frontend", state["frontendIndexSha256"]])
                    else:
                        result["blockers"].append("FRONTEND_CHANGE_SCOPE_NOT_VERIFIED")
                        result["nextAction"] = "manual-contract-or-dependency-review"
    except (OSError, ValueError, subprocess.SubprocessError):
        result["blockers"].append("LIVE_FRONTEND_PROVENANCE_UNVERIFIED")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("deploy", "status", "check", "plan", "plan-live-frontend", "publish", "publish-frontend", "verify", "prepare-isolated", "plan-isolated", "publish-isolated", "verify-isolated"))
    parser.add_argument("--api-only", action="store_true", help="Deploy an already installed reviewed API-only standby")
    parser.add_argument("--change-base", help="Approved prior commit for exact change-scope verification")
    parser.add_argument("--apply", action="store_true", help="Required for publish-frontend")
    parser.add_argument("--frontend-only", action="store_true", help="Approved API-compatible frontend-only release; task counts are informational")
    parser.add_argument("--expected-old-frontend", help="Approved current live index SHA256 for frontend-only publish")
    parser.add_argument("--expected-main-head")
    parser.add_argument("--expected-api-revision")
    parser.add_argument("--expected-frontend-sha256")
    parser.add_argument("--baseline-unknown", type=int, default=BASELINE_PAID_UNKNOWN)
    parser.add_argument("--candidate-root", type=Path, help="Isolated checkout composed of live API base and approved frontend")
    parser.add_argument("--expected-candidate-head", help="Full isolated candidate commit")
    parser.add_argument("--approved-frontend-head", help="Full reviewed frontend source commit, ancestor of main")
    parser.add_argument("--reviewed-api-compatible", action="store_true", help="Explicit approval of the exact frontend/API compatibility")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("--baseline-unknown must be nonnegative")
    if args.command == "deploy":
        from classic_simple_deploy import deploy
        result = deploy(ROOT, apply=args.apply, api_only=args.api_only,
                        reviewed=args.reviewed_api_compatible, expected_main=args.expected_main_head,
                        expected_api=args.expected_api_revision, approved=args.approved_frontend_head)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command == "plan-live-frontend":
        result = plan_live_frontend(ROOT, approved=args.approved_frontend_head)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command == "prepare-isolated":
        if not args.candidate_root or not args.apply:
            parser.error("prepare-isolated requires a new --candidate-root and --apply")
        try:
            result = prepare_isolated(ROOT, args.candidate_root, base=args.change_base,
                approved=args.approved_frontend_head, main=args.expected_main_head,
                api=args.expected_api_revision, old=args.expected_old_frontend)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result = {"ready": False, "blockers": [str(error)], "frontendPublished": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command in {"plan-isolated", "publish-isolated"}:
        if not args.candidate_root:
            parser.error("isolated release requires --candidate-root")
        pins = dict(base=args.change_base, approved=args.approved_frontend_head,
                    main=args.expected_main_head, api=args.expected_api_revision,
                    old=args.expected_old_frontend, candidate_head=args.expected_candidate_head)
        if args.command == "publish-isolated":
            if not args.apply or not args.reviewed_api_compatible:
                parser.error("publish-isolated requires --apply and --reviewed-api-compatible")
            try:
                result = publish_isolated(ROOT, args.candidate_root, lock=activation_lock, **pins)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                result = {"ready": False, "blockers": [str(error)], "frontendPublished": False,
                          "nextAction": "inspect-release-status-and-receipt-before-retry"}
        else:
            result = assess_isolated(ROOT, args.candidate_root, **pins)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command == "verify-isolated":
        # Independent acceptance: verify exact published receipt and active API,
        # not whether unrelated main backend work has been activated.
        result = assess(ROOT, args.expected_main_head, args.expected_api_revision, frontend_only=True)
        result["fullMainApiMatches"] = "BACKEND_MAIN_NOT_ACTIVE" not in result["blockers"]
        result["warnings"] = [] if result["fullMainApiMatches"] else ["UNRELATED_MAIN_API_DRIFT"]
        result["blockers"] = [b for b in result["blockers"] if b != "BACKEND_MAIN_NOT_ACTIVE"]
        if result["admissionPaused"] is not False:
            result["blockers"].append("ADMISSION_NOT_OPEN")
        from classic_isolated_frontend import SHA40, SHA64
        if not all(isinstance(v, str) and SHA40.fullmatch(v) for v in (args.expected_main_head, args.approved_frontend_head)) or not all(
                isinstance(v, str) and SHA64.fullmatch(v) for v in (args.expected_api_revision, args.expected_frontend_sha256)):
            result["blockers"].append("FULL_PINNED_REVISIONS_REQUIRED")
        else:
            try:
                git(ROOT, "merge-base", "--is-ancestor", args.approved_frontend_head, args.expected_main_head)
                if result["frontendIndexSha256"] != args.expected_frontend_sha256 or http_digest() != args.expected_frontend_sha256:
                    result["blockers"].append("FRONTEND_HTTP_MISMATCH")
                elif not receipt_for(ROOT, args.expected_frontend_sha256, args.approved_frontend_head, active=result):
                    result["blockers"].append("LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED")
            except (OSError, ValueError, subprocess.SubprocessError):
                result["blockers"].append("FRONTEND_PROVENANCE_OR_HTTP_UNVERIFIED")
        result["ready"] = not result["blockers"]
        result["releaseMode"] = "isolated-frontend-hot"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command == "plan":
        if not args.change_base:
            parser.error("plan requires --change-base (approved prior Git commit)")
        try:
            result = plan_release(ROOT, base=args.change_base)
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["canHotPublishFrontend"] or result["nextAction"] == "no-deploy-needed" else 2
    if args.command in {"publish", "publish-frontend"}:
        if args.command == "publish":
            if not args.change_base:
                parser.error("publish requires --change-base")
            try:
                scope = committed_changes(ROOT, args.change_base)
            except ValueError as error:
                parser.error(str(error))
            if not scope["frontendHotEligible"]:
                parser.error("Automated publish blocked; classification is " + scope["mode"])
            args.frontend_only = True
        if args.frontend_only and not args.change_base:
            parser.error("frontend-only publish requires --change-base")
        if not args.apply:
            parser.error("publish-frontend requires --apply")
        if not args.expected_main_head or len(args.expected_main_head) != 40:
            parser.error("publish-frontend requires approved full --expected-main-head")
        if not args.expected_api_revision or len(args.expected_api_revision) != 64:
            parser.error("publish-frontend requires approved full --expected-api-revision")
        if args.frontend_only and (not args.expected_old_frontend or len(args.expected_old_frontend) != 64):
            parser.error("frontend-only publish requires --expected-old-frontend")
        result = publish_frontend(ROOT, args.expected_main_head, args.expected_api_revision,
                                  args.baseline_unknown, frontend_only=args.frontend_only,
                                  expected_old_frontend=args.expected_old_frontend,
                                  change_base=args.change_base)
    else:
        result = assess(ROOT, args.expected_main_head, args.expected_api_revision,
                        baseline_unknown=args.baseline_unknown, frontend_only=args.frontend_only)
        if args.command == "verify":
            state = report(ROOT)
            reasons = verify_release(
                state, expected_api_revision=args.expected_api_revision,
                expected_frontend_sha256=args.expected_frontend_sha256,
                expected_main_head=args.expected_main_head,
            )
            result["blockers"] = sorted(set(result["blockers"] + reasons))
            result["ready"] = not result["blockers"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
