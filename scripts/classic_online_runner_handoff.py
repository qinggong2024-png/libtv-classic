"""Reviewed online Runner-only handoff. No provider calls or DB writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import classic_lan
from classic_blue_api import route_operator_lock, supervisor_supports_blue
from classic_green_api import green_process, json_get, read_manifest, source_fingerprint, validated_release, web_route
from classic_online_standby import PROTECTED, installed_slot_root
from classic_worker_checkpoint import (
    atomic_write, owned_record, process_matches, read_identity,
    verify_worker_compatibility,
)
from classic_worker_handoff import restart_held
from classic_worker_lease import exclusive_classic_worker
from classic_worker_source import worker_source

CONFIG_BASE = "9b01a07df26943ac8f59970a89fd757db0acb7cbe0c021f175789b3b3261baf2"
OLD = "2d2b376ce7f26618b1ab404db0edf1f9789b5e00220898590cecfbfc38b80418"
NEW = "dfe908f8b6157024d57e87f7666d89cade213a528f908851ae2a13ff847f0ec8"
API = "9fa2b54595dbb067ebd9c3bc691454e1d9ed9ec072d7527bb7a557e66554f7aa"


# Exact reviewed source pairs. Both API slots must retain the existing
# durable acceptance/checkpoint contract. Do not accept arbitrary hashes.
RELIABILITY_RUNNER = "f095cd9c2460f1a4b39ebc4df742dbd980adaabe2451c4355f906b797cb9bb58"
RELIABILITY_API_SLOTS = {
    8002: "ad6d7b8ed034520060f8f9df71ae9dde35e1e018c037a7ed05215a198d4a8edb",
    8003: "9daa74dc429b49c18f26bdda720ccccaec00641deb2c79d42f8bf46e858e31e6",
}


# Emergency narrow transition: identical worker contract, only Sol error handling.
# The production route is blue and the green process is absent. Do not authorize
# a route change, revive green, or relax receipt/checkpoint protections.
SOL400_RUNNER = "e5e409d3651df975b9f427aa61965d0ccef33e22df577cdb96cb818cdb5168b9"
SOL400_API_SLOTS = {8002: "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61"}
SOL_JPEG_RUNNER = "59cb1fb8fd8e9fa1a1d589d02b2d8dae6f20b75cfff2ae1b3dad7654be14ae45"


# Reviewed narrow Sol transport change; both installed API identities retained.
SOL_STREAM_RUNNER = "d0cd7dae7e9d8bbe3871b0e9a14521ba9ab5e2598533399996a38135a5787e5f"
SOL_STREAM_API_SLOTS = {
    8002: "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61",
    8003: "3ea8fb51840e8a1356e276afabe1841b9aa3fc6c423e247e8872a999d703e206",
}


def reviewed_transition(old, new):
    if (old, new) == (OLD, NEW):
        return {8002: API, 8003: API}
    if (old, new) == (NEW, RELIABILITY_RUNNER):
        return dict(RELIABILITY_API_SLOTS)
    if (old, new) == (RELIABILITY_RUNNER, SOL400_RUNNER):
        return dict(SOL400_API_SLOTS)
    if (old, new) == (SOL400_RUNNER, SOL_JPEG_RUNNER):
        return dict(SOL400_API_SLOTS)
    if (old, new) == (SOL_JPEG_RUNNER, SOL_STREAM_RUNNER):
        return dict(SOL_STREAM_API_SLOTS)
    raise RuntimeError("unreviewed Runner source transition")


# A deliberately narrow reusable review path. Changes to dispatch, billing,
# schema, ownership, model configuration, or other worker code are NOT eligible.
COMPILER_RUNTIME_PATHS = frozenset({
    "backend/src/libtv_agent/adapters/kling_prompt_translator_lite.py",
    "backend/src/libtv_agent/adapters/kling_ootd_audit.py",
})
COMPILER_TEST_PATHS = frozenset({
    "backend/tests/test_kling_translator_lite_release.py",
    "backend/tests/test_ootd_observability_isolated.py",
})


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    ).stdout.strip()


def _is_hash(value, size):
    return isinstance(value, str) and len(value) == size and all(
        c in "0123456789abcdef" for c in value
    )


def reviewed_compiler_transition(previous, release, *, old, new, review):
    """Verify an explicitly reviewed immutable compiler-only transition.

    Neither a valid hash nor a clean tree alone authorizes a handoff. Pin
    both worker revisions, the candidate commit, both API identities, and
    restrict the *entire* committed diff. Existing legacy pairs still work.
    """
    if (set(review) != {"old", "new", "commit", "apiSlots"}
            or not _is_hash(old, 64) or not _is_hash(new, 64)
            or old == new or review["old"] != old or review["new"] != new
            or not _is_hash(review["commit"], 40)
            or set(review["apiSlots"]) != {8002, 8003}
            or not all(_is_hash(v, 64) for v in review["apiSlots"].values())):
        raise RuntimeError("explicit compiler review pins are invalid")
    if source_fingerprint(previous) != old or source_fingerprint(release) != new:
        raise RuntimeError("reviewed Runner source changed")
    base = _git(previous, "rev-parse", "HEAD")
    head = _git(release, "rev-parse", "HEAD")
    if head != review["commit"]:
        raise RuntimeError("reviewed candidate commit changed")
    if _git(previous, "status", "--porcelain", "--untracked-files=all") or _git(
        release, "status", "--porcelain", "--untracked-files=all"
    ):
        raise RuntimeError("reviewed Runner source is not clean")
    try:
        _git(release, "merge-base", "--is-ancestor", base, head)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("candidate must descend from running Runner") from exc
    paths = set(_git(release, "diff", "--name-only", "--no-renames", base, head).splitlines())
    if not paths & COMPILER_RUNTIME_PATHS or not paths <= (
        COMPILER_RUNTIME_PATHS | COMPILER_TEST_PATHS
    ):
        raise RuntimeError("candidate is not a compiler-only change")
    # No added/deleted/renamed/symlink runtime files are admitted. The
    # allowlisted modules must be existing regular source files.
    for path in paths & COMPILER_RUNTIME_PATHS:
        if any(not (root / path).is_file() or (root / path).is_symlink()
               for root in (previous, release)):
            raise RuntimeError("compiler module must remain a regular source file")
    verify_worker_compatibility(previous, release)
    return dict(review["apiSlots"])


def verify_compiler_api_contract(previous, installed):
    verify_worker_compatibility(previous, installed)
    for relative in PROTECTED:
        if (previous / relative).read_bytes() != (installed / relative).read_bytes():
            raise RuntimeError(f"API/Runner contract changed: {relative}")


def verify_route_scope(api_slots):
    route = web_route()
    if route.get("activeApiPort") not in api_slots:
        raise RuntimeError("active API not in reviewed transition")
    if len(api_slots) == 1:
        manifest = read_manifest()
        if (route.get("activeApiPort") != 8002
                or route.get("inFlightByPort", {}).get("8003") != 0
                or route.get("pendingRouteSelections") != 0
                or (manifest and green_process(manifest))):
            raise RuntimeError("single-slot emergency handoff requires inactive green")
    return route


def evidence(path: Path, baseline: int, preserved=None, *, drained=False):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        unknown = db.execute("SELECT COUNT(*) FROM generation_attempt WHERE status='UNKNOWN_AFTER_SUBMIT'").fetchone()[0]
        if unknown > baseline:
            raise RuntimeError("paid UNKNOWN increased")
        active = {}
        for aid, provider, status, task in db.execute(
            "SELECT id,provider,status,task_id FROM generation_attempt WHERE status IN ('RUNNING','SUBMITTING')"
        ):
            if status != "RUNNING" or provider != "kling_official_api" or not task:
                raise RuntimeError("unresolved local or paid submission")
            receipts = db.execute(
                "SELECT provider_task_id FROM generation_submission_attempt WHERE attempt_id=? AND status='ACCEPTED'",
                (aid,),
            ).fetchall()
            if receipts != [(task,)]:
                raise RuntimeError("original paid receipt not unique or not matching")
            active[aid] = task
        for state, aid in db.execute(
            "SELECT status,attempt_id FROM generation_command WHERE status IN ('CLAIMED','LAUNCH_STARTED','PROCESS_STARTED')"
        ):
            if state != "PROCESS_STARTED" or aid not in active:
                raise RuntimeError("local paid command not safe to hand off")
        if drained and db.execute(
            "SELECT COUNT(*) FROM outbox_event WHERE event_type='DURABLE_CONFIRMATION_STARTED'"
        ).fetchone()[0]:
            raise RuntimeError("durable confirmation still executing")
        for aid, task in (preserved or {}).items():
            row = db.execute("SELECT task_id FROM generation_attempt WHERE id=?", (aid,)).fetchone()
            if row != (task,):
                raise RuntimeError("original cloud task identity changed")
            if db.execute(
                "SELECT COUNT(*) FROM generation_submission_attempt WHERE attempt_id=? AND status='ACCEPTED' AND provider_task_id=?",
                (aid, task),
            ).fetchone()[0] != 1:
                raise RuntimeError("original cloud receipt changed")
        return active, unknown


def handoff(release: Path, *, apply=False, compiler_review=None):
    root = classic_lan.ROOT
    runtime = classic_lan.STATE.parent
    db = runtime.parent / "classic.db"
    previous, old = worker_source("runner", runtime, root)
    new = source_fingerprint(release)
    release = validated_release(release, new)

    def reviewed_slots():
        if compiler_review is not None:
            return reviewed_compiler_transition(
                previous, release, old=old, new=new, review=compiler_review,
            )
        return reviewed_transition(old, new)

    api_slots = reviewed_slots()
    verify_worker_compatibility(previous, release)

    def verify_installed_slots():
        if reviewed_slots() != api_slots:
            raise RuntimeError("reviewed transition changed")
        for port, revision in api_slots.items():
            installed = installed_slot_root(root, port, revision)
            if compiler_review is not None:
                verify_compiler_api_contract(previous, installed)
    config_path = runtime / "durable-execution.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_revision = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    marker_path = runtime / "runner-online-handoff.json"
    request_path = runtime / "checkpoint-runner-request.json"
    pin_path = runtime / "worker-runner-pin.json"

    def validate():
        if not supervisor_supports_blue(runtime) or restart_held("runner") or marker_path.exists():
            raise RuntimeError("supervisor or handoff state unavailable")
        verify_route_scope(api_slots)
        verify_installed_slots()
        for port in api_slots:
            gate = json_get(port, "/api/v1/system/classic-new-video-admission")
            durable = json_get(port, "/api/v1/system/classic-durable-execution")
            if (gate.get("deploymentId") != "libtv-classic-lan-0e3d1c8"
                    or gate.get("configured") is not True or gate.get("newVideoAdmissionsPaused") is not False
                    or durable.get("configRevision") != config_revision
                    or durable.get("analysis") != "READY" or durable.get("confirmation") != "READY"):
                raise RuntimeError("reviewed API slots must be ready with open intake")
        if config.get("runnerSourceRevision") != CONFIG_BASE or config.get("analysis") is not True or config.get("confirmation") is not True:
            raise RuntimeError("unexpected durable contract")
        identity, state = read_identity(runtime, "runner")
        if state != "RUNNING" or identity["sourceRevision"] != old or not process_matches(owned_record("runner"), identity):
            raise RuntimeError("original Runner identity not verified")
        if request_path.exists() and json.loads(request_path.read_text(encoding="utf-8")) == identity:
            raise RuntimeError("matching checkpoint already in progress")
        return identity, evidence(db, 15)[0]

    if not apply:
        _, active = validate()
        return {"action": "dry-run", "preservedCloudTasks": len(active),
                "fromRevision": old, "toRevision": new,
                "apiSlots": api_slots, "intakeRemainsOpen": True,
                "singleRunnerCheckpoint": True,
                "scope": "kling-compiler" if compiler_review is not None else "legacy-reviewed-pair"}
    with route_operator_lock(runtime):
        with exclusive_classic_worker("api-blue", runtime):
            identity, active = validate()
            now = time.time()
            marker = {"version": 1, "configRevision": config_revision, "fromRevision": old,
                      "toRevision": new, "checkpoint": identity, "createdAt": now, "expiresAt": now + 180}
            atomic_write(marker_path, marker)
            atomic_write(request_path, identity)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                observed, state = read_identity(runtime, "runner")
                if observed != identity or state == "DRAIN_FAILED":
                    raise RuntimeError("Runner checkpoint identity or result changed; original pin retained")
                if state == "DRAINED" and classic_lan.owned_process(owned_record("runner")) is None:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Runner drain timed out; original pin retained")
            with exclusive_classic_worker("runner", runtime):
                if read_identity(runtime, "runner") != (identity, "DRAINED") or classic_lan.owned_process(owned_record("runner")) is not None:
                    raise RuntimeError("old Runner still owned")
                if json.loads(config_path.read_text(encoding="utf-8")) != config:
                    raise RuntimeError("durable contract changed")
                if worker_source("runner", runtime, root) != (previous, old):
                    raise RuntimeError("Runner pin changed during checkpoint")
                verify_route_scope(api_slots)
                verify_installed_slots()
                evidence(db, 15, active, drained=True)
                validated_release(release, new)
                atomic_write(pin_path, {"version": 1, "releaseRoot": str(release), "sourceRevision": new})
        # Supervisor owns process creation. Never launch a competing worker.
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            try:
                new_identity, state = read_identity(runtime, "runner")
                if (state == "RUNNING" and new_identity["sourceRevision"] == new
                        and process_matches(owned_record("runner"), new_identity)
                        and all(json_get(p, "/api/v1/system/classic-durable-execution").get("analysis") == "READY"
                                and json_get(p, "/api/v1/system/classic-durable-execution").get("confirmation") == "READY"
                                for p in api_slots)):
                    evidence(db, 15, active)
                    if json.loads(marker_path.read_text(encoding="utf-8")) == marker:
                        marker_path.unlink()
                    return {"action": "runner-online-handoff-complete", "preservedCloudTasks": len(active),
                            "sourceRevision": new, "pid": new_identity["pid"]}
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(0.5)
        raise RuntimeError("new Runner readiness not verified; inspect supervisor and pin")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed-compiler-update", action="store_true")
    parser.add_argument("--expected-old")
    parser.add_argument("--expected-new")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-api-8002")
    parser.add_argument("--expected-api-8003")
    args = parser.parse_args()
    pins = (args.expected_old, args.expected_new, args.expected_commit,
            args.expected_api_8002, args.expected_api_8003)
    if args.reviewed_compiler_update != all(pins) or (
        not args.reviewed_compiler_update and any(pins)
    ):
        parser.error("compiler update requires explicit review and all five pins")
    review = None
    if args.reviewed_compiler_update:
        review = {"old": pins[0], "new": pins[1], "commit": pins[2],
                  "apiSlots": {8002: pins[3], 8003: pins[4]}}
    print(json.dumps(handoff(args.release_root, apply=args.apply,
                             compiler_review=review), indent=2))
