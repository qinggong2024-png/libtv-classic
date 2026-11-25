"""Single reviewed candidate delivery through the existing Classic queue.

Only exact API-compatible pairs. Never deploy a different standby, a Worker,
schema, prompt, or frontend change as part of this command.
"""
from pathlib import Path
import subprocess

from classic_active_release_status import workspace_source_revision
from classic_isolated_frontend import SHA40, SHA64, clean, http_digest, live_frontend_baseline, tree
import classic_release as release
import classic_release_manager as manager
from classic_release_classifier import reviewed_pair


def blocked(reason, **extra):
    return {"ready": False, "blockers": [reason], **extra}


def plan(root, candidate, *, expected_main, expected_candidate, expected_api,
         expected_standby):
    root, candidate = root.resolve(), candidate.resolve()
    if not all(isinstance(x, str) and SHA40.fullmatch(x) for x in (expected_main, expected_candidate)):
        return blocked("FULL_MAIN_AND_CANDIDATE_PINS_REQUIRED")
    if not all(isinstance(x, str) and SHA64.fullmatch(x) for x in (expected_api, expected_standby)):
        return blocked("FULL_TARGET_AND_PREVIOUS_STANDBY_PINS_REQUIRED")
    if candidate.parent != root.parent or candidate == root:
        return blocked("CANDIDATE_NOT_ISOLATED")
    if (release.git(root, "branch", "--show-current") != "main" or not clean(root)
            or release.git(root, "rev-parse", "HEAD") != expected_main):
        return blocked("MAIN_CHANGED_OR_DIRTY")
    if not clean(candidate) or release.git(candidate, "rev-parse", "HEAD") != expected_candidate:
        return blocked("CANDIDATE_CHANGED_OR_DIRTY")
    if release.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") != release.git(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        return blocked("CANDIDATE_NOT_SAME_REPOSITORY")
    try:
        release.git(root, "merge-base", "--is-ancestor", expected_main, expected_candidate)
    except subprocess.CalledProcessError:
        return blocked("CANDIDATE_REBASE_RETEST_REQUIRED")
    if workspace_source_revision(candidate) != expected_api:
        return blocked("CANDIDATE_API_FINGERPRINT_CHANGED")
    paths = release.git(root, "diff", "--name-only", expected_main, expected_candidate).splitlines()
    if not paths or any(not (p.startswith("backend/src/") or p.startswith("backend/tests/")) for p in paths):
        return blocked("API_ONLY_CANDIDATE_SCOPE_REQUIRED")
    if tree(root, expected_main) != tree(root, expected_candidate):
        return blocked("FRONTEND_CHANGED")
    state = release.report(root)
    active = state.get("activeApi") or {}
    main = state.get("mainCheckout") or {}
    if (not state.get("ok") or not active.get("diskMatchesRunning") or main.get("matchesRunning") is not True
            or main.get("gitHead") != expected_main or state.get("admissionPaused") is not False):
        return blocked("LIVE_MAIN_API_BASELINE_UNVERIFIED")
    current, target = state.get("activeApiPort"), state.get("standbyApiPort")
    if {current, target} != {8002, 8003}:
        return blocked("INVALID_API_SLOTS")
    if not reviewed_pair(active.get("sourceRevision"), expected_api):
        return blocked("EXACT_API_COMPATIBILITY_REVIEW_REQUIRED")
    pins = dict(current=current, target=target, active=active["sourceRevision"],
                target_revision=expected_api, frontend=state.get("frontendIndexSha256"),
                config=state.get("activeConfigRevision"), schema=state.get("activeSchemaRevision"))
    if (not all(isinstance(pins[k], str) and SHA64.fullmatch(pins[k]) for k in ("frontend", "config"))
            or not pins["schema"] or http_digest() != pins["frontend"]):
        return blocked("LIVE_FRONTEND_OR_CONTRACT_UNVERIFIED")
    proof = live_frontend_baseline(root, state)
    if not proof or tree(root, proof["base"]) != tree(root, expected_main):
        return blocked("LIVE_FRONTEND_PROVENANCE_REQUIRED")
    standby = state.get("standbyApiSourceRevision")
    if standby not in (None, expected_standby, expected_api):
        return blocked("ANOTHER_STANDBY_CANDIDATE_PRESENT")
    if standby is None and target != 8003:
        return blocked("VACANT_BLUE_REQUIRES_SUPERVISOR_REVIEW")
    return {"ready": True, "pins": pins, "main": expected_main, "candidate_head": expected_candidate,
            "previous_standby": expected_standby, "prepare": standby != expected_api,
            "paths": paths, "candidate": str(candidate), "blockers": []}


def prepare(root, candidate, pins, previous):
    # All existing slot guards run again in the independent launcher.
    state = release.report(root)
    if not manager.api_matches(state, pins):
        raise RuntimeError("live API baseline changed")
    standby = state.get("standbyApiSourceRevision")
    if standby == pins["target_revision"]:
        return
    if standby not in (None, previous):
        raise RuntimeError("standby owned by a different candidate")
    from classic_green_operator import dispatch, needs_independent
    common = ["--release-root", str(candidate), "--expected-source-revision", pins["target_revision"],
              "--expected-active-revision", pins["active"], "--expected-standby-revision", previous,
              "--baseline-unknown", "15", "--apply"]
    if pins["target"] == 8003:
        from classic_green_api import replace_green_online, restore_vacant_green_online
        if standby is None:
            restore_vacant_green_online(candidate, pins["target_revision"],
                expected_active=pins["active"], expected_previous=previous, apply=True)
        else:
            replace_green_online(candidate, pins["target_revision"], expected_active=pins["active"],
                expected_standby=None if previous == pins["active"] else previous, apply=True)
    else:
        if standby is None:
            raise RuntimeError("vacant blue requires supervisor review")
        if previous != pins["active"] and not reviewed_pair(pins["active"], previous):
            raise RuntimeError("unreviewed previous standby pair")
        if needs_independent():
            dispatch(["prepare-blue", *common])
        else:
            from classic_blue_api import replace_blue
            replace_blue(candidate, pins["target_revision"], expected_active=pins["active"],
                         expected_standby=None if previous == pins["active"] else previous, baseline_unknown=15, online_prepare=True, apply=True)


def deliver(root, candidate, intent):
    pins = intent["pins"]
    args = dict(expected_main=intent["main"], expected_candidate=intent["candidate_head"],
                expected_api=pins["target_revision"], expected_standby=intent["previous_standby"])
    current = plan(root, candidate, **args)
    if not current["ready"] or current.get("pins") != pins:
        return blocked("CANDIDATE_OR_LIVE_PINS_CHANGED", plan=current)
    # A preparation failure is uncertain, not an excuse to run it twice.
    prepare(root, candidate, pins, intent["previous_standby"])
    return manager.switch_reviewed_api(root, pins, candidate_delivery=intent)


def deploy(root, candidate, *, expected_main, expected_candidate, expected_api,
           expected_standby, apply=False, reviewed=False):
    try:
        preview = plan(root, candidate, expected_main=expected_main,
                       expected_candidate=expected_candidate, expected_api=expected_api,
                       expected_standby=expected_standby)
        if not preview["ready"] or not apply:
            return preview
        if not reviewed:
            return blocked("REVIEW_EXACT_CANDIDATE_AND_API_PAIR_FIRST")
        if any(row["state"] in {"queued", "running", "needs-review"} for row in manager.status(root)):
            return blocked("ANOTHER_RELEASE_PENDING_OR_NEEDS_REVIEW")
        intent = {"kind": "api-candidate", "candidate": preview["candidate"],
                  "main": preview["main"], "candidate_head": preview["candidate_head"],
                  "previous_standby": preview["previous_standby"], "pins": preview["pins"]}
        row = manager.store_intent(root, intent)
        if row["state"] != "queued":
            return blocked("EXISTING_RELEASE_INTENT_REQUIRES_REVIEW", releaseId=row["id"], state=row["state"])
        result = manager.run_next(root, expected_id=row["id"])
        return {"ready": result["state"] == "verified", "releaseId": row["id"], "outcome": result}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return blocked("CHECK_RELEASE_STATUS_BEFORE_RETRY", detail=type(exc).__name__)
