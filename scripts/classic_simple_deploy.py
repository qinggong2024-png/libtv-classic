"""Small operator facade over the existing Classic release tools.

No new daemon or release infrastructure. Preview by default; only the selected
reviewed release is executed. Worker/schema/prompt changes keep their own gates.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import classic_release as release
import classic_release_manager as manager
from classic_isolated_frontend import SHA64, clean, http_digest


def blocked(reason, **extra):
    return dict(ready=False, action="blocked", blockers=[reason], **extra)


def plan_api(root):
    state = release.report(root)
    main = (state.get("mainCheckout") or {}).get("gitHead")
    active = state.get("activeApi") or {}
    current, target = state.get("activeApiPort"), state.get("standbyApiPort")
    if (not state.get("ok") or active.get("diskMatchesRunning") is not True
            or state.get("admissionPaused") is not False):
        return blocked("LIVE_CLASSIC_NOT_READY")
    if release.git(root, "branch", "--show-current") != "main" or not clean(root) or release.git(root, "rev-parse", "HEAD") != main:
        return blocked("PRODUCTION_MAIN_CHANGED_OR_DIRTY")
    if {current, target} != {8002, 8003}:
        return blocked("INVALID_CLASSIC_SLOTS")
    pins = dict(current=current, target=target, active=active.get("sourceRevision"),
                target_revision=state.get("standbyApiSourceRevision"), frontend=state.get("frontendIndexSha256"),
                config=state.get("activeConfigRevision"), schema=state.get("activeSchemaRevision"))
    if not all(isinstance(pins[k], str) and SHA64.fullmatch(pins[k]) for k in ("active", "target_revision", "frontend", "config")) or not pins["schema"]:
        return blocked("API_STANDBY_IDENTITY_UNVERIFIED")
    if http_digest() != pins["frontend"]:
        return blocked("SERVED_FRONTEND_BASELINE_CHANGED")
    if pins["active"] == pins["target_revision"]:
        return dict(ready=True, action="api-already-current", mainHead=main, pins=pins,
                    fullMainApiMatches=(state.get("mainCheckout") or {}).get("matchesRunning") is True)
    return dict(ready=True, action="reviewed-api-only", mainHead=main, pins=pins,
                note="Only the installed, explicitly reviewed API pair may be switched. Existing compatibility gate still runs.")


def deploy(root: Path, *, apply=False, api_only=False, reviewed=False, expected_main=None, expected_api=None):
    start = time.monotonic()
    try:
        if release.git(root, "branch", "--show-current") != "main" or not clean(root):
            return blocked("RUN_FROM_CLEAN_CLASSIC_MAIN")
        plan = plan_api(root) if api_only else release.plan_live_frontend(root)
        if not plan["ready"]:
            return dict(plan, action="blocked")
        if expected_main and plan.get("mainHead") != expected_main:
            return blocked("MAIN_CHANGED_SINCE_REVIEW")
        if expected_api and expected_api != (plan.get("pins", {}).get("target_revision") if api_only else plan.get("activeApiRevision")):
            return blocked("API_CHANGED_SINCE_REVIEW")
        if plan.get("nextAction") == "frontend-already-current" or plan.get("action") == "api-already-current":
            return dict(plan, action="no-change", deployed=False,
                        backendPending=not plan.get("fullMainApiMatches", False))
        if not apply:
            return dict(plan, action="preview", deployed=False)
        if api_only and not expected_api:
            return blocked("PIN_REVIEWED_API_TARGET_FIRST", plan=plan)
        if not reviewed:
            return blocked("REVIEW_EXACT_DIFF_AND_API_COMPATIBILITY_FIRST", plan=plan)
        # Never execute another session's queued intent. The existing durable
        # queue is an implementation detail, not another operator step.
        if any(row["state"] in {"queued", "running", "needs-review"} for row in manager.status(root)):
            return blocked("ANOTHER_RELEASE_PENDING_OR_NEEDS_REVIEW")
        if api_only:
            row = manager.enqueue_api(root, plan["pins"])
        else:
            pins = dict(base=plan["baseline"]["base"], approved=plan["mainHead"], main=plan["mainHead"],
                        api=plan["activeApiRevision"], old=plan["frontendIndexSha256"])
            candidate = root.parent / ("allwe-classic-deploy-" + uuid.uuid4().hex[:12])
            prepared = release.prepare_isolated(root, candidate, **pins)
            if not prepared["ready"]:
                return dict(prepared, action="blocked", deployed=False)
            pins["candidate_head"] = prepared["candidateHead"]
            npm = shutil.which("npm.cmd") or shutil.which("npm")
            if not npm:
                return blocked("NPM_UNAVAILABLE", candidateRoot=str(candidate))
            subprocess.run([npm, "ci", "--prefer-offline", "--no-audit", "--no-fund"],
                           cwd=candidate / "frontend", check=True, timeout=300)
            row = manager.enqueue(root, candidate, pins)
        if row["state"] != "queued":
            return blocked("EXISTING_RELEASE_INTENT_REQUIRES_REVIEW", releaseId=row["id"], state=row["state"])
        result = manager.run_next(root, expected_id=row["id"])
        return dict(ready=result["state"] == "verified", action="deploy", releaseId=row["id"],
                    deployed=result["state"] == "verified", outcome=result,
                    elapsedSeconds=round(time.monotonic() - start, 2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return blocked("CHECK_RELEASE_STATUS_BEFORE_RETRY", detail=str(error),
                       elapsedSeconds=round(time.monotonic() - start, 2))
