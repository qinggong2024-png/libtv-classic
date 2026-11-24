"""Independent frontend release gate: pinned live API, reviewed frontend trees and receipt.

Does not alter routing, admission, workers, database, or paid tasks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from classic_active_release_status import report, workspace_source_revision
from classic_release_classifier import classify_changes, committed_changes

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA64 = re.compile(r"[0-9a-f]{64}")
LIVE_KEYS = ("activeApiPort", "activeApiRevision", "activeApiRoot", "activeApiHead",
             "activeConfigRevision", "activeSchemaRevision", "admissionPaused", "mainHead")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def http_digest(path="/"):
    with build_opener(ProxyHandler({})).open("http://127.0.0.1:5176" + path, timeout=8) as response:
        return hashlib.sha256(response.read()).hexdigest()


def clean(root):
    return not (git(root, "diff", "--numstat", "HEAD", "--")
                or git(root, "ls-files", "--others", "--exclude-standard"))


def tree(root, commit):
    return git(root, "rev-parse", "--verify", commit + ":frontend")


def history(root):
    return root / "var" / "classic-lan" / "runtime" / "release-history"


def receipt_for(root, frontend_hash, base, *, active=None):
    """Only trust a successful local receipt whose actual artifact tree equals the approved base."""
    for path in sorted(history(root).glob("*.json"), reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(item, dict):
                continue
            source = item.get("artifactSourceHead") or item.get("candidateHead")
            if (item.get("frontendNew") == frontend_hash == item.get("servedFrontendSha256")
                    and isinstance(source, str) and SHA40.fullmatch(source)
                    and tree(root, source) == tree(root, base)
                    and (active is None or all(item.get(k) == active.get(k) for k in
                        ("activeApiRevision", "activeApiPort", "activeConfigRevision", "activeSchemaRevision")))):
                return path.name
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def live_frontend_baseline(root, state):
    """Find the last proven frontend ancestor, not the most recent main commit.

    The receipt must bind the actual served entry to the current API contract.
    A receipt from an older API combination is not compatibility approval.
    """
    active = state.get("activeApi") or {}
    main = (state.get("mainCheckout") or {}).get("gitHead")
    live_hash = state.get("frontendIndexSha256")
    pins = {
        "activeApiRevision": active.get("sourceRevision"),
        "activeApiPort": state.get("activeApiPort"),
        "activeConfigRevision": state.get("activeConfigRevision"),
        "activeSchemaRevision": state.get("activeSchemaRevision"),
    }
    if (not state.get("ok") or active.get("diskMatchesRunning") is not True
            or not isinstance(main, str) or not SHA40.fullmatch(main)
            or not isinstance(live_hash, str) or not SHA64.fullmatch(live_hash)
            or not all(pins.values()) or pins["activeApiPort"] not in (8002, 8003)):
        return None
    if http_digest() != live_hash:
        return None
    for path in sorted(history(root).glob("*.json"), reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(item, dict):
                continue
            if (item.get("frontendNew") != live_hash
                    or item.get("servedFrontendSha256") != live_hash
                    or any(item.get(k) != v for k, v in pins.items())):
                continue
            source = item.get("artifactSourceHead") or item.get("candidateHead")
            if not isinstance(source, str) or not SHA40.fullmatch(source):
                continue
            for base in (item.get("approvedFrontendHead"), item.get("mainHead"), source, main):
                if not isinstance(base, str) or not SHA40.fullmatch(base):
                    continue
                try:
                    git(root, "merge-base", "--is-ancestor", base, main)
                    if tree(root, source) == tree(root, base):
                        return {"base": base, "artifactSourceHead": source, "receipt": path.name}
                except (OSError, ValueError, subprocess.SubprocessError):
                    continue
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def frontend_scope(root, base, approved):
    changes = committed_changes(root, base, approved)
    scope = classify_changes([p for p in changes["paths"] if p.startswith("frontend/")])
    scope.update(base=changes["base"], target=changes["target"],
                 excludedMainPaths=[p for p in changes["paths"] if not p.startswith("frontend/")])
    return scope


def assess_isolated(root, candidate, *, base, approved, main, api, old, candidate_head):
    root, candidate = root.resolve(), candidate.resolve()
    state = report(root)
    active = state.get("activeApi") or {}
    result = {
        "releaseMode": "isolated-frontend-hot", "mainHead": (state.get("mainCheckout") or {}).get("gitHead"),
        "activeApiPort": state.get("activeApiPort"), "activeApiRevision": active.get("sourceRevision"),
        "activeApiRoot": active.get("root"), "activeApiHead": active.get("gitHead"),
        "activeConfigRevision": state.get("activeConfigRevision"), "activeSchemaRevision": state.get("activeSchemaRevision"),
        "admissionPaused": state.get("admissionPaused"), "frontendIndexSha256": state.get("frontendIndexSha256"),
        "fullMainApiMatches": (state.get("mainCheckout") or {}).get("matchesRunning") is True,
        "candidateHead": candidate_head, "approvedFrontendHead": approved, "blockers": [],
    }
    errors = result["blockers"]
    if not all(isinstance(v, str) and SHA40.fullmatch(v) for v in (base, approved, main, candidate_head)) or not all(
            isinstance(v, str) and SHA64.fullmatch(v) for v in (api, old)):
        errors.append("FULL_PINNED_REVISIONS_REQUIRED")
        result["ready"] = False
        return result
    if not state.get("ok") or active.get("diskMatchesRunning") is not True:
        errors.append("LIVE_CLASSIC_UNHEALTHY_OR_API_DISK_DRIFT")
    if state.get("userUrl") != "http://192.168.30.126:5176/" or state.get("activeApiPort") not in (8002, 8003):
        errors.append("INVALID_CLASSIC_ROUTE")
    if result["activeApiRevision"] != api:
        errors.append("API_REVISION_CHANGED")
    if result["mainHead"] != main:
        errors.append("MAIN_COMMIT_CHANGED")
    if not result["activeConfigRevision"] or not result["activeSchemaRevision"] or result["admissionPaused"] is not False:
        errors.append("LIVE_CONFIG_SCHEMA_OR_ADMISSION_UNVERIFIED")
    if result["frontendIndexSha256"] != old:
        errors.append("FRONTEND_BASELINE_CHANGED")
    try:
        if http_digest() != old:
            errors.append("SERVED_FRONTEND_BASELINE_CHANGED")
    except OSError:
        errors.append("SERVED_FRONTEND_UNAVAILABLE")
    try:
        if candidate == root or (active.get("root") and candidate == Path(active["root"]).resolve()):
            errors.append("CANDIDATE_NOT_ISOLATED")
        if git(root, "branch", "--show-current") != "main" or git(root, "rev-parse", "HEAD") != main or not clean(root):
            errors.append("PRODUCTION_MAIN_CHANGED_OR_DIRTY")
        if git(candidate, "rev-parse", "HEAD") != candidate_head or not clean(candidate):
            errors.append("CANDIDATE_CHANGED_OR_DIRTY")
        # The candidate must be composed solely of reviewed main frontend and the running API base.
        if git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir"):
            errors.append("CANDIDATE_NOT_SAME_REPOSITORY")
        git(root, "merge-base", "--is-ancestor", approved, main)
        # Mixed main history is safe only because the candidate's non-frontend
        # tree and API fingerprint are pinned to the running version.
        scope = frontend_scope(root, base, approved)
        result["changes"] = scope
        if not scope["frontendHotEligible"]:
            errors.append("FRONTEND_CHANGE_SCOPE_NOT_VERIFIED")
        if tree(candidate, candidate_head) != tree(root, approved):
            errors.append("CANDIDATE_FRONTEND_NOT_APPROVED_TREE")
        active_head = active.get("gitHead")
        if not active_head or git(candidate, "diff", "--name-only", active_head, candidate_head, "--", ".", ":(exclude)frontend/**"):
            errors.append("CANDIDATE_NON_FRONTEND_DIFFERS_FROM_ACTIVE")
        if workspace_source_revision(candidate) != api:
            errors.append("CANDIDATE_API_FINGERPRINT_MISMATCH")
        proof = receipt_for(root, old, base)
        result["baselineReceipt"] = proof
        if not proof:
            errors.append("LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED")
    except (OSError, ValueError, subprocess.SubprocessError):
        errors.append("CANDIDATE_OR_BASE_PROVENANCE_UNVERIFIED")
    result["ready"] = not errors
    result["warnings"] = [] if result["fullMainApiMatches"] else ["UNRELATED_MAIN_API_DRIFT"]
    return result



def prepare_isolated(root, candidate, *, base, approved, main, api, old):
    """Compose a new local worktree, without modifying production or existing worktrees."""
    root, candidate = root.resolve(), candidate.resolve()
    state = report(root)
    active = state.get("activeApi") or {}
    if not all(isinstance(v, str) and SHA40.fullmatch(v) for v in (base, approved, main, active.get("gitHead"))) or not all(
            isinstance(v, str) and SHA64.fullmatch(v) for v in (api, old)):
        raise ValueError("FULL_PINNED_REVISIONS_REQUIRED")
    if (not state.get("ok") or active.get("diskMatchesRunning") is not True
            or active.get("sourceRevision") != api
            or (state.get("mainCheckout") or {}).get("gitHead") != main
            or state.get("frontendIndexSha256") != old or http_digest() != old):
        raise ValueError("LIVE_BASELINE_CHANGED")
    if candidate.exists() or candidate.is_relative_to(root) or root.is_relative_to(candidate):
        raise ValueError("CANDIDATE_PATH_MUST_BE_NEW_AND_ISOLATED")
    if not active.get("root") or candidate.is_relative_to(Path(active["root"]).resolve()):
        raise ValueError("CANDIDATE_PATH_MUST_NOT_TOUCH_ACTIVE_API")
    if git(root, "branch", "--show-current") != "main" or git(root, "rev-parse", "HEAD") != main or not clean(root):
        raise ValueError("PRODUCTION_MAIN_CHANGED_OR_DIRTY")
    git(root, "merge-base", "--is-ancestor", approved, main)
    if not frontend_scope(root, base, approved)["frontendHotEligible"]:
        raise ValueError("FRONTEND_CHANGE_SCOPE_NOT_VERIFIED")
    if not receipt_for(root, old, base):
        raise ValueError("LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED")
    git(root, "worktree", "add", "--detach", str(candidate), active["gitHead"])
    git(candidate, "restore", "--source", approved, "--staged", "--worktree", "--", "frontend")
    if git(candidate, "diff", "--cached", "--name-only"):
        git(candidate, "-c", "user.name=ALLWE Release", "-c", "user.email=release@example.invalid",
            "commit", "-m", "release(classic): compose reviewed isolated frontend")
    head = git(candidate, "rev-parse", "HEAD")
    result = assess_isolated(root, candidate, base=base, approved=approved, main=main, api=api,
                             old=old, candidate_head=head)
    result["candidateRoot"] = str(candidate)
    result["nextAction"] = "install-locked-dependencies-test-and-publish" if result["ready"] else "inspect-isolated-candidate"
    return result


def unchanged(before, after):
    return all(before.get(k) == after.get(k) for k in LIVE_KEYS)


def staged_files(stage, dist):
    """Never overwrite immutable assets or public files used by existing tabs."""
    files = {}
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise ValueError("STAGED_SYMLINK")
        if not path.is_file():
            continue
        relative = path.relative_to(stage).as_posix()
        files[relative] = digest(path)
        dest = dist / relative
        if relative != "index.html" and dest.exists() and digest(dest) != files[relative]:
            if relative.startswith("assets/"):
                raise ValueError("IMMUTABLE_ASSET_COLLISION")
            # Preserve runtime public files; an explicit changed public asset is not this release mode.
            raise ValueError("PUBLIC_FILE_CHANGE_REQUIRES_REVIEW")
    if not files.get("index.html") or not any(k.startswith("assets/") for k in files):
        raise ValueError("INCOMPLETE_FRONTEND_BUILD")
    index = (stage / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)=["\x27]/(assets/[^"\x27?#]+)', index)
    if not refs or any(ref not in files for ref in refs):
        raise ValueError("MISSING_ENTRY_ASSET")
    return files


def atomic_index(dest, data):
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".index-", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def record_receipt(root, item):
    archive = history(root)
    archive.mkdir(parents=True, exist_ok=True)
    # Windows clock resolution can yield identical timestamps for two receipts.
    # Publish a complete receipt under a unique name without replacing history.
    path = archive / ("frontend-release-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                      + "-" + uuid.uuid4().hex + ".json")
    with tempfile.NamedTemporaryFile(dir=archive, prefix=".receipt-", delete=False) as output:
        pending = Path(output.name)
    try:
        with pending.open("w", encoding="utf-8") as output:
            json.dump(item, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.link(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return str(path)


@contextmanager
def candidate_stage_lock(candidate):
    # Different candidates can build concurrently. The same candidate cannot.
    path = Path(git(candidate, "rev-parse", "--absolute-git-dir")) / "classic-stage.lock"
    with path.open("x", encoding="ascii") as handle:
        try:
            handle.write(str(os.getpid()))
            handle.flush()
            yield
        finally:
            handle.close()
            path.unlink(missing_ok=True)


def publish_isolated(root, candidate, *, lock, **pins):
    before = assess_isolated(root, candidate, **pins)
    if not before["ready"]:
        return before
    with candidate_stage_lock(candidate):
        dist = root / "frontend" / "dist"
        stage = candidate / "frontend" / "dist-build"
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise RuntimeError("npm not found")
        # Compile in the isolated candidate before taking the production lock.
        # No production assets are written during staging. Pin the staged bytes.
        subprocess.run([npm, "run", "build"], cwd=candidate / "frontend", check=True, timeout=240)
        files = staged_files(stage, dist)
        with lock(root):
            if staged_files(stage, dist) != files:
                raise RuntimeError("STAGED_ARTIFACT_CHANGED")
            after = assess_isolated(root, candidate, **pins)
            if not after["ready"] or not unchanged(before, after):
                after["ready"] = False
                after["blockers"].append("LIVE_OR_CANDIDATE_BASELINE_CHANGED")
                return after
            entry = dist / "index.html"
            archive = history(root)
            archive.mkdir(parents=True, exist_ok=True)
            backup = archive / ("frontend-index-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".html")
            shutil.copy2(entry, backup)
            expected = files["index.html"]
            # Install new immutable assets first, then recheck every staged/live file.
            for name, sha in files.items():
                if name == "index.html":
                    continue
                dest = dist / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    # Never leave a half-written immutable filename after a failed copy.
                    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".asset-", delete=False) as output:
                        pending = Path(output.name)
                    try:
                        shutil.copyfile(stage / name, pending)
                        if digest(pending) != sha:
                            raise RuntimeError("STAGED_ASSET_CHANGED")
                        try:
                            os.link(pending, dest)
                        except FileExistsError:
                            pass
                    finally:
                        pending.unlink(missing_ok=True)
                if digest(dest) != sha:
                    raise RuntimeError("FRONTEND_ASSET_MISMATCH")
            final = assess_isolated(root, candidate, **pins)
            if not final["ready"] or not unchanged(before, final):
                final["ready"] = False
                final["blockers"].append("LIVE_OR_CANDIDATE_BASELINE_CHANGED")
                return final
            data = (stage / "index.html").read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError("STAGED_INDEX_CHANGED")
            switched = False
            try:
                atomic_index(entry, data)
                switched = True
                post = assess_isolated(root, candidate, **dict(pins, old=expected))
                # New index has no receipt yet; it will be recorded only after verification.
                remaining = [b for b in post["blockers"] if b != "LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED"]
                if remaining or not unchanged(before, post) or digest(entry) != expected or http_digest() != expected:
                    raise RuntimeError("FRONTEND_POST_PUBLISH_VERIFICATION_FAILED")
                for name, sha in files.items():
                    if name.startswith("assets/") and http_digest("/" + name) != sha:
                        raise RuntimeError("FRONTEND_HTTP_ASSET_MISMATCH")
                checked = assess_isolated(root, candidate, **dict(pins, old=expected))
                if [b for b in checked["blockers"] if b != "LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED"] or not unchanged(before, checked):
                    raise RuntimeError("LIVE_BASELINE_CHANGED_AFTER_ASSET_CHECK")
                result = dict(checked, blockers=[], ready=True, frontendPublished=True,
                              mode="isolated-frontend-hot", frontendOld=pins["old"], frontendNew=expected,
                              expectedFrontendSha256=expected, servedFrontendSha256=expected,
                              artifactSourceHead=pins["candidate_head"], rollbackIndex=str(backup),
                              artifactFilesVerified=len(files))
                result["baselineReceipt"] = before["baselineReceipt"]
                result["receipt"] = record_receipt(root, result)
                return result
            except Exception:
                # Only restore our own pointer; never overwrite a concurrent publisher.
                if switched and digest(entry) == expected:
                    atomic_index(entry, backup.read_bytes())
                raise
