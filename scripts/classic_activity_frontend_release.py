"""Narrow reviewed activity API + frontend release, preserving live workers.

Do not use this for arbitrary source revisions. The isolated candidate is based
on the active API and the verified previously published frontend. Never build
or publish unpublished worker code from main.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from classic_active_release_status import report, workspace_source_revision
from classic_blue_api import route_operator_lock
from classic_green_api import json_get
from classic_online_standby import ACTIVE_WORK, installed_slot_root
from classic_release import ROOT, git, release_lock
from classic_isolated_frontend import staged_files, http_digest, receipt_for, record_receipt
from classic_v2_canary_preflight import check

API = "c2c53ddf70c12ba45b9f21371b341fff2f0b5fa2418e30a1494d56e590c11844"
CANDIDATE = "cd500f89765bd9be4e3405bd70af0a7d3672001b"
FRONTEND_BASE = "c7bbfd6db5d1a09ac33bbfe1e79ab6312fb1fd92"
APPROVED_FRONTEND = "f6e5e165a960992eb1698fb6fb90a180c6e8251a"
OLD_INDEX = "85d0aad956a15f0921fee3396d1e6fe6de2f48f504ace3d64d62f190dfb92da6"
FRONTEND_PATHS = {
    "frontend/src/api.ts",
    "frontend/src/types.ts",
    "frontend/src/features/settings/AccountEventsPanel.tsx",
    "frontend/src/features/settings/AccountEventsPanel.test.tsx",
    "frontend/src/features/settings/accountEvents.css",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_candidate(root, candidate, expected_main):
    if git(root, "branch", "--show-current") != "main" or git(root, "rev-parse", "HEAD") != expected_main:
        raise RuntimeError("main identity changed")
    if git(root, "status", "--porcelain"):
        raise RuntimeError("production main is dirty")
    if git(candidate, "rev-parse", "HEAD") != CANDIDATE:
        raise RuntimeError("candidate Git identity changed")
    if git(candidate, "status", "--porcelain"):
        raise RuntimeError("candidate is dirty")
    if workspace_source_revision(candidate) != API:
        raise RuntimeError("candidate API fingerprint changed")
    # Later unrelated main commits may remain unpublished. The old served
    # index is still pinned; never replace another session's published UI.
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                    APPROVED_FRONTEND, expected_main], check=True, timeout=10)
    if git(candidate, "rev-parse", "HEAD:frontend") != git(root, "rev-parse", APPROVED_FRONTEND + ":frontend"):
        raise RuntimeError("candidate frontend differs from approved main ancestor")
    paths = set(git(candidate, "diff", "--name-only", FRONTEND_BASE, CANDIDATE, "--", "frontend").splitlines())
    if paths != FRONTEND_PATHS:
        raise RuntimeError("frontend delta not exactly the reviewed activity change")


def check_live(root, candidate, expected_main, expected_index):
    verify_candidate(root, candidate, expected_main)
    state = report(root)
    active = state.get("activeApi") or {}
    if (not state.get("ok") or state.get("admissionPaused") is not False
            or state.get("activeApiPort") != 8003
            or active.get("sourceRevision") != API
            or active.get("diskMatchesRunning") is not True
            or Path(active.get("root", "")).resolve() != candidate.resolve()
            or state.get("frontendIndexSha256") != expected_index
            or not state.get("activeConfigRevision") or not state.get("activeSchemaRevision")):
        raise RuntimeError("live API, admission, configuration, or frontend identity changed")
    if installed_slot_root(root, 8003, API).resolve() != candidate.resolve():
        raise RuntimeError("active API process pin mismatch")
    durable = json_get(8003, "/api/v1/system/classic-durable-execution")
    if durable.get("analysis") != "READY" or durable.get("confirmation") != "READY":
        raise RuntimeError("durable Runner not ready")
    tasks = check(root / "var/classic-lan/classic.db", baseline_uncertain=15)
    if not isinstance(tasks.get("blockers"), list) or set(tasks["blockers"]) - ACTIVE_WORK:
        raise RuntimeError("paid-task safety check blocked: " + str(tasks.get("blockers")))
    return state


def publish(root, candidate, expected_main, expected_stage, apply=False):
    stage = candidate / "frontend/dist-build"
    if sha(stage / "index.html") != expected_stage:
        raise RuntimeError("staged index fingerprint mismatch")
    with release_lock(root), route_operator_lock(root / "var/classic-lan/runtime"):
        before = check_live(root, candidate, expected_main, OLD_INDEX)
        proof = receipt_for(root, OLD_INDEX, FRONTEND_BASE)
        if not proof:
            raise RuntimeError("old frontend receipt not verified")
        files = staged_files(stage, root / "frontend/dist")
        if files.get("index.html") != expected_stage:
            raise RuntimeError("staged index changed")
        if not apply:
            return {"ready": True, "action": "dry-run"}
        live = root / "frontend/dist"
        archive = root / "var/classic-lan/runtime/release-history"
        archive.mkdir(parents=True, exist_ok=True)
        backup = archive / ("frontend-index-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".html")
        shutil.copy2(live / "index.html", backup)
        if sha(backup) != OLD_INDEX:
            raise RuntimeError("rollback index fingerprint mismatch")
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node unavailable")
        script = (
            "import {publishClassicFrontend} from './scripts/publish_classic_frontend.mjs';"
            "await publishClassicFrontend({stageDir:process.argv[1],distDir:process.argv[2]});"
        )
        try:
            subprocess.run([node, "--input-type=module", "-e", script, str(stage), str(live)],
                           cwd=root, check=True, timeout=120)
            after = check_live(root, candidate, expected_main, expected_stage)
            for key in ("activeApiPort", "activeConfigRevision", "activeSchemaRevision"):
                if after[key] != before[key]:
                    raise RuntimeError("live contract changed during publication")
            with build_opener(ProxyHandler({})).open("http://127.0.0.1:5176/", timeout=8) as response:
                served = hashlib.sha256(response.read()).hexdigest()
            if served != expected_stage or sha(live / "index.html") != expected_stage:
                raise RuntimeError("frontend served fingerprint mismatch")
            for name, digest in files.items():
                if sha(live / name) != digest:
                    raise RuntimeError("published file fingerprint mismatch")
                if name.startswith("assets/") and http_digest("/" + name) != digest:
                    raise RuntimeError("served asset fingerprint mismatch")
            result = {"mode": "isolated-frontend-hot", "frontendPublished": True,
                      "frontendOld": OLD_INDEX, "frontendNew": served,
                      "servedFrontendSha256": served, "expectedFrontendSha256": expected_stage,
                      "mainHead": expected_main, "approvedFrontendHead": APPROVED_FRONTEND,
                      "candidateHead": CANDIDATE, "artifactSourceHead": CANDIDATE,
                      "baseHead": FRONTEND_BASE, "activeApiPort": 8003,
                      "activeApiRevision": API, "admissionPaused": False,
                      "activeConfigRevision": after["activeConfigRevision"],
                      "activeSchemaRevision": after["activeSchemaRevision"],
                      "rollbackIndex": str(backup), "baselineReceipt": proof,
                      "artifactFilesVerified": len(files), "at": datetime.now(UTC).isoformat()}
            result["receipt"] = record_receipt(root, result)
        except Exception:
            # Roll back only the HTML entry and only if it is still ours.
            if sha(live / "index.html") == expected_stage:
                temp = live / (".rollback-" + backup.stem + ".tmp")
                shutil.copy2(backup, temp)
                temp.replace(live / "index.html")
            raise
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--expected-stage", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(ROOT, args.candidate.resolve(), args.expected_main,
                             args.expected_stage, args.apply), indent=2))
