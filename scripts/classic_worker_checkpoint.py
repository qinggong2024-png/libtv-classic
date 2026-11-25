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



# Narrow, reviewed CUT-only handoff. It does not change the Runner pin,
# API route, durable configuration, paid commands, or remote CUT process.
CUT_PAIR = frozenset({
    "9b01a07df26943ac8f59970a89fd757db0acb7cbe0c021f175789b3b3261baf2",
    "cb00c3d4cbd130720c5c89b827f764295f7fd28b6ec80357534f85d24bcd8b55",
})


# Same verified NAS v2 contract, with read-only SMB status timeout isolation.
CUT_NAS_PAIR = frozenset({
    "9b01a07df26943ac8f59970a89fd757db0acb7cbe0c021f175789b3b3261baf2",
    "c5f6e150e79f1789c3899028d0fdc4d311e1b3f4c2d6169ee11a260bcfb64d8a",
})


# Short SQLite write transactions only; remote CUT and billing identity unchanged.
CUT_AUTO_SWEEP_TX_PAIR = frozenset({
    "c5f6e150e79f1789c3899028d0fdc4d311e1b3f4c2d6169ee11a260bcfb64d8a",
    "f6349c36dce04e2c3a203d527217bc9c47783dc3f2f31cae201fad9293e3ca93",
})


# Exact worker-only candidate 0013094, derived from the live dfe6cc3 baseline.
# Reviewed scope: four CUT/NAS runtime files, unchanged schema and paid identity.
CUT_NAS_RECONCILE_PAIR = frozenset({
    "f6349c36dce04e2c3a203d527217bc9c47783dc3f2f31cae201fad9293e3ca93",
    "4e26f1401f4003d9740d5522cddf208a502ef4d4921a3f3f6fe9536a6478ad7a",
})


def cutflow_checkpoint_evidence(db_path: Path, *, drained: bool) -> dict:
    import sqlite3

    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        terminal = "'FAILED','CANCELED','PENDING_REVIEW','COMPLETED'"
        single = db.execute(
            f"SELECT COUNT(*) FROM cutflow_handoff WHERE status NOT IN ({terminal})"
        ).fetchone()[0]
        leases = sum(db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE lease_token IS NOT NULL OR lease_until IS NOT NULL"
        ).fetchone()[0] for table in ("cutflow_handoff", "multi_cutflow_task"))
        rows = db.execute(
            f"SELECT id,status,manifest_path,manifest_json FROM multi_cutflow_task "
            f"WHERE status NOT IN ({terminal})"
        ).fetchall()
        if single or (drained and leases):
            raise RuntimeError("CUT local/legacy operation is not safe to hand off")
        evidence = {}
        for identifier, state, path, raw in rows:
            manifest = json.loads(raw or "{}")
            if state == "QUEUED" and not path:
                continue
            digest = manifest.get("manifestSha256")
            if (state not in {"QUEUED", "READY", "ACCEPTED", "EDITING", "IMPORTING", "RUNNING"}
                    or not path or manifest.get("contractVersion") != "libtv-cut-lan-multi/2"
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)):
                raise RuntimeError("CUT original manifest identity is not verifiable")
            evidence[identifier] = (path, digest)
        return {"activeRemoteTasks": len(rows), "localLeases": leases,
                "published": evidence}


def verify_preserved_cutflow(db_path: Path, published: dict) -> None:
    import sqlite3

    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        for identifier, (path, digest) in published.items():
            row = db.execute(
                "SELECT manifest_path,manifest_json FROM multi_cutflow_task WHERE id=?",
                (identifier,),
            ).fetchone()
            if (not row or row[0] != path
                    or json.loads(row[1] or "{}").get("manifestSha256") != digest):
                raise RuntimeError("original CUT task/manifest changed during checkpoint")


def online_cutflow_handoff(release: Path, revision: str, *, expected_old: str,
                           baseline_unknown: int, apply: bool = False) -> dict:
    """Drain only local CUT operations; preserve original remote task identity."""
    import time

    from classic_blue_api import route_operator_lock
    from classic_cutflow import cutflow_environment
    from classic_green_api import json_get, web_route
    from classic_v2_canary_preflight import check
    from classic_worker_source import worker_source

    if frozenset((expected_old, revision)) not in (CUT_PAIR, CUT_NAS_PAIR, CUT_AUTO_SWEEP_TX_PAIR, CUT_NAS_RECONCILE_PAIR):
        raise RuntimeError("CUT handoff requires the reviewed exact source pair")
    runtime = classic_lan.STATE.parent
    db_path = runtime.parent / "classic.db"
    release = validated_release(release, revision)

    def validate():
        if not supervisor_supports_blue(runtime) or restart_held("cutflow"):
            raise RuntimeError("CUT supervisor/hold identity unavailable")
        route = web_route()
        gate = json_get(route["activeApiPort"], "/api/v1/system/classic-new-video-admission")
        if (gate.get("configured") is not True
                or gate.get("deploymentId") != "libtv-classic-lan-0e3d1c8"
                or gate.get("newVideoAdmissionsPaused") is not False):
            raise RuntimeError("expected open Classic intake")
        settings = cutflow_environment(runtime.parent)
        if (settings.get("CUTFLOW_TRANSPORT") != "lan"
                or settings.get("CUTFLOW_MULTI_SOURCE_ENABLED") != "true"
                or settings.get("CUTFLOW_NAS_STATUS_ENABLED") != "true"):
            raise RuntimeError("requires durable NAS CUT v2 transport")
        previous, old_revision = worker_source("cutflow", runtime, classic_lan.ROOT)
        if old_revision != expected_old:
            raise RuntimeError("CUT worker source pin changed")
        verify_worker_compatibility(previous, release)
        task_report = check(db_path, baseline_uncertain=baseline_unknown)
        if task_report["counts"]["uncertainPaidHistorical"] > baseline_unknown:
            raise RuntimeError("paid UNKNOWN increased")
        identity, state = read_identity(runtime, "cutflow")
        if state != "RUNNING" or identity["sourceRevision"] != expected_old or not process_matches(
            owned_record("cutflow"), identity
        ):
            raise RuntimeError("CUT worker identity changed")
        request = runtime / "checkpoint-cutflow-request.json"
        if request.exists() and json.loads(request.read_text(encoding="utf-8")) == identity:
            raise RuntimeError("CUT checkpoint already requested")
        return identity, cutflow_checkpoint_evidence(db_path, drained=False)

    if not apply:
        _, evidence = validate()
        return {"action": "dry-run", "activeRemoteTasks": evidence["activeRemoteTasks"],
                "localLeases": evidence["localLeases"], "sourceRevision": revision}
    # The live supervisor also holds api-blue for process recovery. Keep it
    # excluded until the original worker has exited and the new pin is committed.
    # If anything fails before the pin, the original pin remains authoritative.
    with route_operator_lock(runtime), exclusive_classic_worker("api-blue", runtime):
        identity, evidence = validate()
        atomic_write(runtime / "checkpoint-cutflow-request.json", identity)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            observed, state = read_identity(runtime, "cutflow")
            if observed != identity or state == "DRAIN_FAILED":
                raise RuntimeError("CUT checkpoint failed or identity changed")
            if state == "DRAINED" and classic_lan.owned_process(owned_record("cutflow")) is None:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("CUT checkpoint timed out; original pin retained")
        with exclusive_classic_worker("cutflow", runtime):
            if read_identity(runtime, "cutflow") != (identity, "DRAINED"):
                raise RuntimeError("CUT checkpoint receipt changed")
            if classic_lan.owned_process(owned_record("cutflow")) is not None:
                raise RuntimeError("old CUT worker still alive")
            cutflow_checkpoint_evidence(db_path, drained=True)
            verify_preserved_cutflow(db_path, evidence["published"])
            validated_release(release, revision)
            atomic_write(runtime / "worker-cutflow-pin.json",
                         {"version": 1, "releaseRoot": str(release), "sourceRevision": revision})
    return {"action": "cutflow-pin-installed", "sourceRevision": revision,
            "previousRevision": expected_old, "preservedRemoteTasks": len(evidence["published"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("request", "pin", "online-cutflow"))
    parser.add_argument("--kind", choices=("runner", "cutflow"), required=True)
    parser.add_argument("--expected-old", required=True)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--expected-revision")
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    if args.action == "online-cutflow":
        if args.kind != "cutflow" or not args.release_root or not args.expected_revision:
            parser.error("online-cutflow requires kind=cutflow and approved release identity")
        result = online_cutflow_handoff(
            args.release_root, args.expected_revision, expected_old=args.expected_old,
            baseline_unknown=args.baseline_unknown, apply=args.apply,
        )
    elif args.action == "request":
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
