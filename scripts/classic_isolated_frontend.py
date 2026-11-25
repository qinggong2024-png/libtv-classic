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

from classic_active_release_status import report, workspace_source_revision, frontend_release_ready, valid_classic_user_url
from classic_release_classifier import classify_changes, committed_changes, reviewed_api_path

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
    if (not frontend_release_ready(state) or active.get("diskMatchesRunning") is not True
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


def recover_frontend_receipt(root, state, *, apply=False):
    """Rebind a verified publication through exact, previously reviewed API pairs.

    Read-only unless invoked under the existing activation lock. No build, route,
    worker or database changes. Never infer compatibility from matching schema alone.
    """
    active = state.get("activeApi") or {}
    main = (state.get("mainCheckout") or {}).get("gitHead")
    live_hash = state.get("frontendIndexSha256")
    target = active.get("sourceRevision")
    config, schema = state.get("activeConfigRevision"), state.get("activeSchemaRevision")
    port = state.get("activeApiPort")
    if (not frontend_release_ready(state) or active.get("diskMatchesRunning") is not True
            or state.get("admissionPaused") is not False or port not in (8002, 8003)
            or not isinstance(main, str) or not SHA40.fullmatch(main)
            or not isinstance(live_hash, str) or not SHA64.fullmatch(live_hash)
            or not config or not schema or http_digest() != live_hash):
        return None
    dist = root / "frontend" / "dist"
    for path in sorted(history(root).glob("*.json"), reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8-sig"))
            if (not isinstance(item, dict) or item.get("ready") is not True
                    or item.get("frontendPublished") is not True
                    or item.get("frontendNew") != live_hash or item.get("servedFrontendSha256") != live_hash
                    or item.get("activeConfigRevision") != config or item.get("activeSchemaRevision") != schema):
                continue
            chain = reviewed_api_path(item.get("activeApiRevision"), target)
            if chain is None:
                continue
            source = item.get("artifactSourceHead") or item.get("candidateHead")
            base = item.get("approvedFrontendHead") or item.get("mainHead")
            if not all(isinstance(v, str) and SHA40.fullmatch(v) for v in (source, base)):
                continue
            git(root, "merge-base", "--is-ancestor", base, main)
            if tree(root, source) != tree(root, base):
                continue
            files = item.get("artifactFileDigests")
            if not isinstance(files, dict):
                # Older verified receipts stored only the count. Recover the exact
                # original staged bytes from its recorded Git worktree, not main.
                files = None
                for entry in git(root, "worktree", "list", "--porcelain").split("\n\n"):
                    fields = entry.splitlines()
                    if not fields or not fields[0].startswith("worktree ") or "HEAD " + source not in fields:
                        continue
                    stage = Path(fields[0][9:]) / "frontend" / "dist-build"
                    if stage.is_dir() and (stage / "index.html").is_file() and digest(stage / "index.html") == live_hash:
                        files = staged_files(stage, dist)
                        break
            if (not isinstance(files, dict) or files.get("index.html") != live_hash
                    or len(files) != item.get("artifactFilesVerified")
                    or not any(name.startswith("assets/") for name in files)):
                continue
            if any(not isinstance(name, str) or not name or "\\" in name or ":" in name
                   or any(part in ("", ".", "..") for part in name.split("/"))
                   or not isinstance(sha, str) or not SHA64.fullmatch(sha)
                   or not (dist / name).is_file() or (dist / name).is_symlink()
                   or digest(dist / name) != sha for name, sha in files.items()):
                continue
            if any(http_digest("/" + name) != sha for name, sha in files.items() if name.startswith("assets/")):
                continue
            # Recheck the live tuple after potentially slow asset reads.
            if apply:
                after = report(root)
                if not frontend_release_ready(after) or any(after.get(k) != state.get(k) for k in (
                         "activeApiPort", "activeApi", "activeConfigRevision", "activeSchemaRevision",
                        "admissionPaused", "frontendIndexSha256", "mainCheckout")) or http_digest() != live_hash:
                    return None
            receipt = {"mode": "api-only-compatibility-reverified", "ready": True,
                       "frontendPublished": False, "frontendNew": live_hash, "frontendOld": live_hash,
                       "servedFrontendSha256": live_hash, "artifactSourceHead": source,
                       "approvedFrontendHead": base, "mainHead": main, "priorReceipt": path.name,
                       "previousApiRevision": chain[0], "reviewedApiPath": chain,
                       "activeApiRevision": target, "activeApiPort": port,
                       "activeConfigRevision": config, "activeSchemaRevision": schema,
                       "artifactFilesVerified": len(files), "artifactFileDigests": files}
            if apply:
                saved = record_receipt(root, receipt)
                return {"base": base, "artifactSourceHead": source, "receipt": Path(saved).name,
                        "compatibilityRecovered": True, "reviewedApiPath": chain}
            return {"base": base, "artifactSourceHead": source, "receipt": path.name,
                    "recoveryRequired": True, "reviewedApiPath": chain, "artifactFilesVerified": len(files)}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            continue
    return None


def frontend_scope(root, base, approved, *, api=None):
    changes = committed_changes(root, base, approved)
    scope = classify_changes([p for p in changes["paths"] if p.startswith("frontend/")])
    # Exact reviewed Classic status-presentation release (2026-11-24).
    # The three domain modules are pure UI projections of existing API fields;
    # no request, wire type, dependency, schema, or worker was changed.
    # This is NOT a blanket domain-directory exception. Any different frontend
    # tree or running API fingerprint goes through the original classifier.
    if (scope["mode"] == "api-canary-review"
            and api == "ad6d7b8ed034520060f8f9df71ae9dde35e1e018c037a7ed05215a198d4a8edb"
            and tree(root, base) == "7b84c98f36b42b52c261c376cecb6f3d4544318c"
            and tree(root, approved) == "e6117fd78ea61caa10bd164d9934058a18dd745d"):
        scope.update(mode="frontend-hot", frontendHotEligible=True,
                     exactPresentationReview="classic-status-20261124")
        scope["notes"] += " Exact reviewed presentation-only tree and API pins; all isolated release gates still apply."
    # Reviewed deep-video/Kling progress and product-reference labels, 2026-11-25.
    # Existing live API already returns the two timing stages and recorded model fields.
    # The exact candidate changes only UI projections/tests, not request or wire types.
    if (scope["mode"] == "api-canary-review"
            and api == "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61"
            and tree(root, base) == "e6117fd78ea61caa10bd164d9934058a18dd745d"
            and tree(root, approved) == "cccbed2c7a74453cb9806c69aeaef7c3538d4f72"):
        scope.update(mode="frontend-hot", frontendHotEligible=True,
                     exactPresentationReview="classic-deep-video-20261125")
        scope["notes"] += " Exact reviewed deep-video presentation tree and active API pins; all isolated release gates still apply."
    # Exact paired read-only admin media API and inspection frontend; no broad
    # exemption for api.ts changes. Existing active API must already match.
    if (scope["mode"] == "api-canary-review"
            and api == "c97c2e7b73546abd3adc57c9d72548522102ce8af712257e1c71a3a3fec64393"
            and tree(root, base) == "c677e56d9b5483764a7f94a263db2c247eb2ae34"
            and tree(root, approved) == "45187ebfed768292f4b7dd51a6f83227699827dd"):
        scope.update(mode="frontend-hot", frontendHotEligible=True,
                     exactPresentationReview="admin-deep-trace-20261125")
        scope["notes"] += " Exact reviewed admin read-only media and trace API pair."
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
    if not frontend_release_ready(state) or active.get("diskMatchesRunning") is not True:
        errors.append("LIVE_CLASSIC_UNHEALTHY_OR_API_DISK_DRIFT")
    if not valid_classic_user_url(state.get("userUrl")) or state.get("activeApiPort") not in (8002, 8003):
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
        scope = frontend_scope(root, base, approved, api=api)
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
    result["warnings"] = list(state.get("frontendWarnings", [])) + ([] if result["fullMainApiMatches"] else ["UNRELATED_MAIN_API_DRIFT"])
    return result



def prepare_isolated(root, candidate, *, base, approved, main, api, old):
    """Compose a new local worktree, without modifying production or existing worktrees."""
    root, candidate = root.resolve(), candidate.resolve()
    state = report(root)
    active = state.get("activeApi") or {}
    if not all(isinstance(v, str) and SHA40.fullmatch(v) for v in (base, approved, main, active.get("gitHead"))) or not all(
            isinstance(v, str) and SHA64.fullmatch(v) for v in (api, old)):
        raise ValueError("FULL_PINNED_REVISIONS_REQUIRED")
    if (not frontend_release_ready(state) or active.get("diskMatchesRunning") is not True
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
    if not frontend_scope(root, base, approved, api=api)["frontendHotEligible"]:
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


def frontend_build_inputs(root, receipt_name):
    """Use only the explicit, local, allowlisted build setting; bind it to live proof."""
    if not receipt_name or Path(receipt_name).name != receipt_name:
        raise ValueError("FRONTEND_BUILD_BASELINE_REQUIRED")
    receipt = json.loads((history(root) / receipt_name).read_text(encoding="utf-8-sig"))
    live = receipt.get("frontendNew")
    key = "VITE_FEEDBACK_URL"
    config_path = root / "var/classic-lan/runtime/frontend-build-env.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.is_file() else {}
    if not isinstance(config, dict) or set(config) - {key} or not isinstance(config.get(key, ""), str):
        raise ValueError("FRONTEND_BUILD_INPUT_INVALID")
    value = config.get(key, "")
    if value and (not value.startswith(("https://", "http://")) or value != value.strip() or any(c in value for c in "\r\n")):
        raise ValueError("FRONTEND_BUILD_INPUT_INVALID")
    actual = {key: hashlib.sha256(value.encode("utf-8")).hexdigest()}
    # API-only continuity receipts can omit build inputs. Recover only from the
    # exact same served artifact, never from a different frontend build.
    proven = []
    for path in history(root).glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
            if row.get("frontendNew") == live == row.get("servedFrontendSha256") and row.get("buildInputDigests") is not None:
                proven.append(row["buildInputDigests"])
        except (OSError, ValueError, AttributeError):
            continue
    if any(pin != actual for pin in proven) or (value and not proven):
        raise ValueError("FRONTEND_BUILD_INPUT_CHANGED_REVIEW_REQUIRED")
    # Even an empty explicit value overrides ambient shells and candidate .env files.
    env = dict(os.environ)
    env[key] = value
    return env, actual


def verified_public_digests(root, receipt_name):
    """Follow only API continuity receipts for the identical frontend artifact."""
    seen = set()
    expected = None
    artifact = None
    while isinstance(receipt_name, str) and Path(receipt_name).name == receipt_name and receipt_name not in seen:
        seen.add(receipt_name)
        try:
            item = json.loads((history(root) / receipt_name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        if not isinstance(item, dict):
            return {}
        served = item.get("servedFrontendSha256")
        if not isinstance(served, str) or not SHA64.fullmatch(served) or served != item.get("frontendNew"):
            return {}
        if expected is None:
            expected = served
            artifact = item.get("artifactSourceHead") or item.get("candidateHead")
        elif served != expected or (item.get("artifactSourceHead") or item.get("candidateHead")) != artifact:
            return {}
        pins = item.get("artifactFileDigests")
        if isinstance(pins, dict) and pins:
            return pins
        if (item.get("mode") != "api-only-compatibility" or item.get("frontendPublished") is not False
                or item.get("frontendOld") != expected or not artifact):
            return {}
        receipt_name = item.get("priorReceipt")
    return {}


def preserve_verified_public_svg(root, stage, dist, receipt_name):
    """Keep byte-identical live public SVGs when Git checkout only changed EOLs."""
    if not receipt_name or Path(receipt_name).name != receipt_name:
        return []
    verified = verified_public_digests(root, receipt_name)
    kept = []
    for path in stage.rglob("*.svg"):
        name = path.relative_to(stage).as_posix()
        if name.startswith("assets/") or path.is_symlink():
            continue
        live = dist / name
        if not live.is_file() or live.is_symlink():
            continue
        new, old = path.read_bytes(), live.read_bytes()
        if new == old or new.replace(b"\r\n", b"\n") != old.replace(b"\r\n", b"\n"):
            continue
        sha = hashlib.sha256(old).hexdigest()
        if verified.get(name) != sha or http_digest("/" + name) != sha:
            continue
        path.write_bytes(old)  # only the isolated build, never production
        kept.append(name)
    return kept


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


def refresh_main_pin(root, pins):
    """Advance only a main pin, never an approved tree, candidate or live API pin."""
    current = git(root, "rev-parse", "HEAD")
    old = pins["main"]
    if current == old:
        return pins
    try:
        if git(root, "branch", "--show-current") != "main" or not clean(root):
            return pins
        git(root, "merge-base", "--is-ancestor", old, current)
        git(root, "merge-base", "--is-ancestor", pins["approved"], old)
        if tree(root, current) != tree(root, old):
            return pins
    except (OSError, ValueError, subprocess.SubprocessError):
        return pins
    return dict(pins, main=current)


def publish_isolated(root, candidate, *, lock, allow_main_advance=False, **pins):
    requested_main = pins["main"]
    if allow_main_advance:
        pins = refresh_main_pin(root, pins)
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
        build_env, build_input_digests = frontend_build_inputs(root, before["baselineReceipt"])
        subprocess.run([npm, "run", "build"], cwd=candidate / "frontend", env=build_env, check=True, timeout=240)
        kept_public = preserve_verified_public_svg(root, stage, dist, before["baselineReceipt"])
        files = staged_files(stage, dist)
        with lock(root):
            if allow_main_advance:
                pins = refresh_main_pin(root, pins)
                # Compare all live contract fields; the only permitted change is
                # a fast-forward whose complete frontend tree is unchanged.
                before = dict(before, mainHead=pins["main"])
            if frontend_build_inputs(root, before["baselineReceipt"])[1] != build_input_digests:
                raise RuntimeError("FRONTEND_BUILD_INPUT_CHANGED")
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
                              artifactFilesVerified=len(files), artifactFileDigests=files, preservedPublicSvg=kept_public)
                result["buildInputDigests"] = build_input_digests
                result["baselineReceipt"] = before["baselineReceipt"]
                result["requestedMainHead"] = requested_main
                result["receipt"] = record_receipt(root, result)
                return result
            except Exception:
                # Only restore our own pointer; never overwrite a concurrent publisher.
                if switched and digest(entry) == expected:
                    atomic_index(entry, backup.read_bytes())
                raise
