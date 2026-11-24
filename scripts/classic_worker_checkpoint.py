"""Strict Classic worker checkpoint controller, dry-run by default.

This is a local operator tool, not an endpoint. It never terminates a process,
changes routing, deletes task data, or retries a paid command. Active cloud/CUT
handoff is deliberately not enabled until its separate release gate is approved.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import classic_lan
import psutil
from classic_blue_api import supervisor_supports_blue
from classic_green_api import validated_release
from classic_worker_handoff import arm_handoff, preflight, restart_held
from classic_worker_lease import exclusive_classic_worker


def atomic_write(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_identity(runtime: Path, kind: str) -> tuple[dict, str]:
    if kind not in ("runner", "cutflow"):
        raise ValueError("invalid worker kind")
    status = json.loads((runtime / f"checkpoint-{kind}-status.json").read_text(encoding="utf-8"))
    state = status.pop("state")
    if (
        set(status) != {"version", "kind", "pid", "created", "token", "sourceRevision"}
        or type(status["version"]) is not int
        or status["version"] != 1
        or status["kind"] != kind
        or type(status["pid"]) is not int
        or not isinstance(status["created"], (float, int))
        or not isinstance(status["token"], str)
        or len(status["token"]) != 32
        or not isinstance(status["sourceRevision"], str)
        or len(status["sourceRevision"]) != 64
        or any(c not in "0123456789abcdef" for c in status["sourceRevision"])
        or any(c not in "0123456789abcdef" for c in status["token"])
        or state not in ("RUNNING", "DRAINING", "DRAINED", "DRAIN_FAILED")
    ):
        raise RuntimeError("invalid worker checkpoint identity")
    return status, state


def owned_record(kind: str) -> dict:
    stack = json.loads(classic_lan.STATE.read_text(encoding="utf-8"))
    if stack.get("apiPort") != 8002 or stack.get("webPort") != 5176:
        raise RuntimeError("unexpected Classic deployment identity")
    rows = [r for r in stack["processes"] if r.get("name") == kind]
    if len(rows) != 1:
        raise RuntimeError("worker stack record missing or ambiguous")
    return rows[0]


def process_matches(record: dict, identity: dict) -> bool:
    owner = classic_lan.owned_process(record)
    if owner is None:
        return False
    try:
        candidates = [owner, *owner.children(recursive=True)]
        return any(
            p.pid == identity["pid"] and abs(p.create_time() - identity["created"]) < 0.1
            for p in candidates
        )
    except psutil.Error:
        return False


def request_checkpoint(
    kind: str, expected_revision: str, *, baseline_unknown: int, apply: bool = False
) -> dict:
    runtime = classic_lan.STATE.parent

    def validate():
        if not supervisor_supports_blue(runtime):
            raise RuntimeError("running supervisor has no compatible handoff lock")
        preflight(uncertain_baseline=baseline_unknown)
        identity, state = read_identity(runtime, kind)
        if (
            state != "RUNNING"
            or identity["sourceRevision"] != expected_revision
            or not process_matches(owned_record(kind), identity)
        ):
            raise RuntimeError("current worker identity is not verified")
        return identity

    if not apply:
        identity = validate()
        return {"action": "dry-run", "kind": kind, "pid": identity["pid"]}
    with exclusive_classic_worker("api-blue", runtime):
        identity = validate()
        arm_handoff(uncertain_baseline=baseline_unknown, kind=kind)
        if validate() != identity:
            raise RuntimeError("worker identity changed; restart hold retained")
        atomic_write(runtime / f"checkpoint-{kind}-request.json", identity)
        return {"action": "checkpoint-requested", "kind": kind, "pid": identity["pid"]}


def verify_worker_compatibility(root: Path, release: Path) -> None:
    """Until P3, refuse dependency/schema changes in a worker-only replacement."""
    required = (
        "pyproject.toml",
        "uv.lock",
        "backend/alembic.ini",
        "backend/src/libtv_agent/domain/models.py",
        "backend/src/libtv_agent/domain/enums.py",
    )
    for relative in required:
        before, after = root / relative, release / relative
        if not before.is_file() or not after.is_file() or before.read_bytes() != after.read_bytes():
            raise RuntimeError(f"worker-only replacement requires matching {relative}")
    directory = "backend/alembic"
    before_files = {p.relative_to(root) for p in (root / directory).rglob("*.py")}
    after_files = {p.relative_to(release) for p in (release / directory).rglob("*.py")}
    if before_files != after_files or any(
        (root / path).read_bytes() != (release / path).read_bytes() for path in before_files
    ):
        raise RuntimeError("worker-only replacement requires identical migrations")


def install_pin(
    kind: str,
    release: Path,
    revision: str,
    *,
    expected_old: str,
    baseline_unknown: int,
    apply: bool = False,
) -> dict:
    runtime = classic_lan.STATE.parent
    release = validated_release(release, revision)
    verify_worker_compatibility(classic_lan.ROOT, release)

    def validate():
        if not supervisor_supports_blue(runtime) or not restart_held(kind):
            raise RuntimeError("supervisor/worker restart hold unavailable")
        preflight(uncertain_baseline=baseline_unknown)
        identity, state = read_identity(runtime, kind)
        request = json.loads(
            (runtime / f"checkpoint-{kind}-request.json").read_text(encoding="utf-8")
        )
        if state != "DRAINED" or request != identity or identity["sourceRevision"] != expected_old:
            raise RuntimeError("matching old-worker checkpoint receipt is missing")
        if classic_lan.owned_process(owned_record(kind)) is not None:
            raise RuntimeError("old worker has not exited")

    if not apply:
        validate()
        return {"action": "dry-run", "kind": kind, "sourceRevision": revision}
    with exclusive_classic_worker("api-blue", runtime), exclusive_classic_worker(kind, runtime):
        validate()
        release = validated_release(release, revision)
        verify_worker_compatibility(classic_lan.ROOT, release)
        atomic_write(
            runtime / f"worker-{kind}-pin.json",
            {"version": 1, "releaseRoot": str(release), "sourceRevision": revision},
        )
        # Leave the hold intact. A later explicit disarm lets the existing
        # supervisor start the reviewed backend with the original storage.
        return {"action": "pinned-restart-held", "kind": kind, "sourceRevision": revision}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("request", "pin"))
    parser.add_argument("--kind", choices=("runner", "cutflow"), required=True)
    parser.add_argument("--expected-old", required=True)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--expected-revision")
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    if args.action == "request":
        result = request_checkpoint(
            args.kind, args.expected_old, baseline_unknown=args.baseline_unknown, apply=args.apply
        )
    else:
        if not args.release_root or not args.expected_revision:
            parser.error("pin requires an approved immutable release root and fingerprint")
        result = install_pin(
            args.kind,
            args.release_root,
            args.expected_revision,
            expected_old=args.expected_old,
            baseline_unknown=args.baseline_unknown,
            apply=args.apply,
        )
    print(json.dumps(result, indent=2))
