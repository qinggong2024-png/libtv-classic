"""Classic 5176 standby API lifecycle. Never starts a second Runner/CUT worker.

All commands target ONLY the original classic instance's trusted runtime and
loopback 8003. Automatic recovery reuses the pinned immutable release root;
there is no automatic routing fallback and no paid POST.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import psutil
from classic_lan import PYTHON, ROOT, STORAGE, configured_lan_ip, lan_environment

MANIFEST = STORAGE / "runtime" / "green-api.json"
ROUTE = STORAGE / "runtime" / "api-routing.json"
GREEN_PORT = 8003
BLUE_PORT = 8002
DEPLOYMENT = "libtv-classic-lan-0e3d1c8"


def json_get(port: int, path: str) -> dict:
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=4) as reply:
        if reply.status != 200:
            raise RuntimeError("classic API readiness failed")
        return json.load(reply)


def web_route() -> dict:
    status = json_get(5176, "/__classic_api_route/status")
    if (
        status.get("hotRouteVersion") != 1
        or status.get("configured") is not True
        or status.get("allowedApiPorts") != [BLUE_PORT, GREEN_PORT]
        or status.get("activeApiPort") not in {BLUE_PORT, GREEN_PORT}
    ):
        raise RuntimeError("installed classic web server is not hot-route enabled")
    return status


def green_process(manifest: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(manifest["pid"])
        if abs(process.create_time() - manifest["created"]) > 0.1:
            return None
        command = " ".join(process.cmdline()).lower().replace("\\", "/")
        source = str(Path(manifest["releaseRoot"]).resolve()).lower().replace("\\", "/")
        if "uvicorn" not in command or source not in command or "--port 8003" not in command:
            return None
        return (
            process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
        )
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        return None


def source_fingerprint(release_root: Path) -> str:
    source = release_root / "backend" / "src"
    if not source.is_dir() or not (release_root / "pyproject.toml").is_file():
        raise RuntimeError("release root is missing backend source")
    if (release_root / ".env").exists() or list((release_root / "frontend").glob(".env*")):
        raise RuntimeError("release root must not override classic credentials")
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from libtv_agent.config import _workspace_source_revision

    return _workspace_source_revision(release_root)


def read_manifest() -> dict | None:
    try:
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or type(raw.get("pid")) is not int
        or not isinstance(raw.get("releaseRoot"), str)
        or not isinstance(raw.get("sourceRevision"), str)
    ):
        raise RuntimeError("invalid green API process manifest; manual review needed")
    return raw


def write_manifest(data: dict) -> None:
    temporary = MANIFEST.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(MANIFEST)


def validated_release(release_root: Path, expected_fingerprint: str) -> Path:
    release_root = release_root.resolve()
    if release_root == ROOT.resolve():
        raise RuntimeError("green API requires a separate immutable release directory")
    if release_root.parent != ROOT.resolve().parent:
        raise RuntimeError("green API release must be a sibling of the classic source")
    if len(expected_fingerprint) != 64 or any(
        c not in "0123456789abcdef" for c in expected_fingerprint
    ):
        raise RuntimeError("green API requires an exact 64-character source fingerprint")
    if source_fingerprint(release_root) != expected_fingerprint:
        raise RuntimeError("green API source files changed since approval")
    return release_root


def _delegate_green(action, release=None, revision=None, *, active=None, previous=None, baseline=15):
    from classic_green_operator import needs_independent, dispatch
    if not needs_independent():
        return None
    argv = [action]
    if release is not None:
        argv += ["--release-root", str(release), "--expected-source-revision", revision]
    if active is not None:
        argv += ["--expected-active-revision", active]
    if previous is not None:
        argv += ["--expected-standby-revision", previous]
    if action in {"prepare-online", "restore-vacant-online"}:
        argv += ["--baseline-unknown", str(baseline), "--apply"]
    return dispatch(argv)


def start_green(release_root: Path, expected_fingerprint: str, *, recovering: bool = False) -> dict:
    delegated = _delegate_green("recover") if recovering else _delegate_green("start", release_root, expected_fingerprint)
    if delegated is not None:
        return delegated
    # The manual operator and autostart supervisor may race after a crash.
    # Only one is allowed to create a green API process and its PID record.
    from classic_worker_lease import exclusive_classic_worker

    with exclusive_classic_worker("api-green", STORAGE / "runtime"):
        return _start_green_locked(release_root, expected_fingerprint, recovering=recovering)


def _start_green_locked(
    release_root: Path, expected_fingerprint: str, *, recovering: bool = False,
    online_prepare: bool = False,
) -> dict:
    from classic_green_operator import needs_independent
    if needs_independent():
        raise RuntimeError("green API launch requires independent normal-user process")
    route = web_route()
    if route["activeApiPort"] == GREEN_PORT and not recovering:
        raise RuntimeError("cannot start a second green API while the green route is active")
    blue = json_get(BLUE_PORT, "/api/v1/system/classic-new-video-admission")
    if blue.get("deploymentId") != DEPLOYMENT or blue.get("configured") is not True:
        raise RuntimeError("classic intake gate is not installed")
    if not recovering and not online_prepare and blue.get("newVideoAdmissionsPaused") is not True:
        raise RuntimeError("pause new classic video admission before starting standby")
    existing = read_manifest()
    if existing is not None and green_process(existing):
        raise RuntimeError("green API already running; cannot duplicate")
    if existing is not None and not recovering:
        raise RuntimeError("stale green API manifest requires operator review")
    release_root = validated_release(release_root, expected_fingerprint)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", GREEN_PORT)) == 0:
            raise RuntimeError("green API port already occupied by an unknown process")
    lan_ip = configured_lan_ip()
    if lan_ip is None:
        raise RuntimeError("Classic site address not configured")
    env = lan_environment(BLUE_PORT, 5176, lan_ip)
    env["APP_PORT"] = str(GREEN_PORT)
    env["PYTHONPATH"] = str(release_root / "backend" / "src")
    # Full source fingerprint is generated from the immutable code at startup.
    env.pop("DEPLOYMENT_SOURCE_REVISION", None)
    logs = STORAGE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    args = [
        str(PYTHON),
        "-m",
        "uvicorn",
        "libtv_agent.app:create_app",
        "--factory",
        "--app-dir",
        str(release_root / "backend" / "src"),
        "--host",
        "127.0.0.1",
        "--port",
        str(GREEN_PORT),
    ]
    with (logs / "api-green.log").open("ab") as handle:
        process = subprocess.Popen(
            args,
            cwd=release_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0,
        )
    recorded = {
        "version": 1,
        "pid": process.pid,
        "created": psutil.Process(process.pid).create_time(),
        "releaseRoot": str(release_root),
        "sourceRevision": expected_fingerprint,
        "lastStartUtc": datetime.now(UTC).isoformat(),
        "restartCount": (existing or {}).get("restartCount", 0) + (1 if recovering else 0),
    }
    write_manifest(recorded)
    # This endpoint checks schema and DB; green must be ready before routing.
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("standby green API exited during startup")
        try:
            json_get(GREEN_PORT, "/api/v1/health/ready")
            status = json_get(GREEN_PORT, "/api/v1/system/status")
            if status.get("sourceRevision") == expected_fingerprint:
                return recorded
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("standby green API did not pass readiness and source verification")


def maybe_recover_green() -> bool:
    manifest = read_manifest()
    if manifest is None or green_process(manifest):
        return False
    route = web_route()
    if route["activeApiPort"] != GREEN_PORT:
        # An unused standby is not automatically restarted.
        return False
    last = datetime.fromisoformat(manifest["lastStartUtc"])
    if (datetime.now(UTC) - last).total_seconds() < 60:
        # Bound hot crash loops. Route fails closed until the next retry.
        return False
    if manifest.get("restartCount", 0) >= 3:
        raise RuntimeError("green API crash retry budget exhausted")
    start_green(Path(manifest["releaseRoot"]), manifest["sourceRevision"], recovering=True)
    return True


def stop_green() -> None:
    if read_manifest() is None:
        return
    from classic_blue_api import route_operator_lock
    from classic_worker_lease import exclusive_classic_worker

    runtime = STORAGE / "runtime"
    with route_operator_lock(runtime), exclusive_classic_worker("api-green", runtime):
        _stop_green_locked()


def _stop_green_locked() -> None:
    manifest = read_manifest()
    if manifest is None:
        return
    route = web_route()
    if route["activeApiPort"] == GREEN_PORT:
        raise RuntimeError("cannot stop the API currently serving the classic site")
    active = route.get("inFlightByPort", {}).get(str(GREEN_PORT))
    if active != 0:
        raise RuntimeError("green API still has in-flight requests")
    if route.get("drainContractVersion") == 1:
        from classic_api_drain import inspect

        if not inspect(GREEN_PORT, manifest["sourceRevision"])["ready"]:
            raise RuntimeError("green API process-local work has not drained")
    process = green_process(manifest)
    if process:
        process.terminate()
        try:
            process.wait(timeout=10)
        except psutil.TimeoutExpired as exc:
            raise RuntimeError("green API did not exit; manual review required") from exc
    MANIFEST.unlink(missing_ok=True)


def replace_green_online(release: Path, revision: str, *, expected_active: str,
                         baseline_unknown: int = 15, apply: bool = False,
                         expected_standby: str | None = None) -> dict:
    """Explicit preparation only; preserve the current blue route and workers."""
    from classic_blue_api import route_operator_lock, supervisor_supports_blue
    from classic_online_standby import guard, verify_prepared, reviewed_pair
    from classic_worker_lease import exclusive_classic_worker

    runtime = STORAGE / "runtime"
    release = validated_release(release, revision)

    def validate():
        if not supervisor_supports_blue(runtime):
            raise RuntimeError("running supervisor has no compatible slot contract")
        if expected_standby is not None:
            if (not reviewed_pair(expected_active, expected_standby)
                    or not reviewed_pair(expected_active, revision)):
                raise RuntimeError("standby replacement requires reviewed exact source pairs")
            return guard(ROOT, release, revision, expected_active, GREEN_PORT, baseline_unknown,
                         expected_standby=expected_standby)
        return guard(ROOT, release, revision, expected_active, GREEN_PORT, baseline_unknown)

    if not apply:
        validate()
        return {"action": "dry-run", "slot": GREEN_PORT, "sourceRevision": revision}
    delegated = _delegate_green("prepare-online", release, revision, active=expected_active,
                                previous=expected_standby, baseline=baseline_unknown)
    if delegated is not None:
        return delegated
    with route_operator_lock(runtime), exclusive_classic_worker("api-green", runtime):
        active, previous = validate()
        manifest = read_manifest()
        if (not manifest or manifest["sourceRevision"] != previous["sourceRevision"]
                or not green_process(manifest)):
            raise RuntimeError("existing green standby identity unavailable")
        validate()
        _stop_green_locked()
        _start_green_locked(release, revision, online_prepare=True)
        verify_prepared(GREEN_PORT, revision, active)
        return {"action": "standby-prepared", "slot": GREEN_PORT,
                "sourceRevision": revision, "previousRevision": previous["sourceRevision"]}


def restore_vacant_green_online(release: Path, revision: str, *, expected_active: str,
                                expected_previous: str, baseline_unknown: int = 15,
                                apply: bool = False) -> dict:
    """Prepare an absent green process, never route or stop a running process."""
    from classic_blue_api import route_operator_lock, supervisor_supports_blue
    from classic_online_standby import ACTIVE_WORK, verify_candidate, verify_prepared, reviewed_pair
    from classic_release import release_lock
    from classic_worker_lease import exclusive_classic_worker
    from classic_v2_canary_preflight import check

    runtime = STORAGE / "runtime"
    release = validated_release(release, revision)

    def validate():
        if not reviewed_pair(expected_active, revision) or not supervisor_supports_blue(runtime):
            raise RuntimeError("vacant standby requires reviewed pair and supervisor")
        verify_candidate(ROOT, release, expected_active, revision)
        route = web_route()
        if (route.get("activeApiPort") != BLUE_PORT or route.get("drainContractVersion") != 1
                or route.get("pendingRouteSelections") != 0 or route.get("inFlightByPort", {}).get(str(GREEN_PORT)) != 0):
            raise RuntimeError("vacant green route not verified")
        manifest = read_manifest()
        if not manifest or manifest.get("sourceRevision") != expected_previous or green_process(manifest):
            raise RuntimeError("previous green manifest or process not verified vacant")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", GREEN_PORT)) != (10061 if os.name == "nt" else 111):
                raise RuntimeError("green port not proven closed")
        active = json_get(BLUE_PORT, "/api/v1/system/status")
        gate = json_get(BLUE_PORT, "/api/v1/system/classic-new-video-admission")
        durable = json_get(BLUE_PORT, "/api/v1/system/classic-durable-execution")
        drain = json_get(BLUE_PORT, "/api/v1/system/classic-api-drain")
        if (active.get("sourceRevision") != expected_active or not active.get("configRevision") or not active.get("schemaRevision")
                or gate.get("gateVersion") != 1 or gate.get("deploymentId") != DEPLOYMENT or gate.get("configured") is not True
                or gate.get("newVideoAdmissionsPaused") is not False
                or durable.get("contractVersion") != 1 or durable.get("deploymentId") != DEPLOYMENT
                or durable.get("analysis") != "READY" or durable.get("confirmation") != "READY" or not durable.get("configRevision")
                or drain.get("contractVersion") != 1 or drain.get("recoveryLockVersion") != 1
                or drain.get("deploymentId") != DEPLOYMENT or drain.get("port") != BLUE_PORT
                or drain.get("sourceRevision") != expected_active or drain.get("ownsRecovery") is not True
                or drain.get("durableAnalysis") is not True or drain.get("durableConfirmation") is not True):
            raise RuntimeError("active API recovery or durable contract not verified")
        tasks = check(STORAGE / "classic.db", baseline_uncertain=baseline_unknown)
        if not isinstance(tasks.get("blockers"), list) or set(tasks["blockers"]) - ACTIVE_WORK:
            raise RuntimeError("paid task safety preflight failed")
        route = web_route()
        if (route.get("activeApiPort") != BLUE_PORT or route.get("pendingRouteSelections") != 0
                or route.get("inFlightByPort", {}).get(str(GREEN_PORT)) != 0):
            raise RuntimeError("route changed during vacant standby inspection")
        return active, manifest

    if not apply:
        validate()
        return {"action": "dry-run", "slot": GREEN_PORT, "sourceRevision": revision}
    delegated = _delegate_green("restore-vacant-online", release, revision, active=expected_active,
                                previous=expected_previous, baseline=baseline_unknown)
    if delegated is not None:
        return delegated
    with release_lock(ROOT), route_operator_lock(runtime), exclusive_classic_worker("api-green", runtime):
        active, manifest = validate()
        # Retain exact stale identity; never invoke stop on an unowned process.
        archive = runtime / "release-history"
        archive.mkdir(parents=True, exist_ok=True)
        previous = archive / ("vacant-green-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        raw = MANIFEST.read_bytes()
        if json.loads(raw) != manifest or green_process(manifest):
            raise RuntimeError("green manifest changed before recovery")
        with previous.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        MANIFEST.unlink()
        try:
            _start_green_locked(release, revision, online_prepare=True)
            verify_prepared(GREEN_PORT, revision, active)
        except Exception:
            # If no new manifest was installed, restore the old forensic record.
            if not MANIFEST.exists():
                with MANIFEST.open("xb") as handle:
                    handle.write(raw)
            raise
        return {"action": "vacant-standby-prepared", "slot": GREEN_PORT,
                "sourceRevision": revision, "previousManifest": str(previous)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "start", "stop", "recover", "prepare-online", "restore-vacant-online"])
    parser.add_argument("--release-root")
    parser.add_argument("--expected-source-revision")
    parser.add_argument("--expected-active-revision")
    parser.add_argument("--expected-standby-revision")
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    if args.action == "status":
        manifest = read_manifest()
        print(
            json.dumps(
                {
                    "route": web_route(),
                    "green": manifest,
                    "greenProcessAlive": bool(manifest and green_process(manifest)),
                },
                indent=2,
            )
        )
    elif args.action == "start":
        if not args.release_root or not args.expected_source_revision:
            parser.error("start requires --release-root and --expected-source-revision")
        print(
            json.dumps(
                start_green(Path(args.release_root), args.expected_source_revision), indent=2
            )
        )
    elif args.action == "restore-vacant-online":
        if not all((args.release_root, args.expected_source_revision, args.expected_active_revision, args.expected_standby_revision)):
            parser.error("restore-vacant-online requires pinned candidate, active and previous manifest revisions")
        print(json.dumps(restore_vacant_green_online(
            Path(args.release_root), args.expected_source_revision, expected_active=args.expected_active_revision,
            expected_previous=args.expected_standby_revision, baseline_unknown=args.baseline_unknown,
            apply=args.apply), indent=2))
    elif args.action == "prepare-online":
        if not (args.release_root and args.expected_source_revision
                and args.expected_active_revision):
            parser.error("prepare-online requires pinned release and active revisions")
        print(json.dumps(replace_green_online(
            Path(args.release_root), args.expected_source_revision,
            expected_active=args.expected_active_revision,
            baseline_unknown=args.baseline_unknown, apply=args.apply,
            expected_standby=args.expected_standby_revision,
        ), indent=2))
    elif args.action == "stop":
        stop_green()
        print("Green API stopped without touching active blue API.")
    else:
        print(json.dumps({"recovered": maybe_recover_green()}))


if __name__ == "__main__":
    main()
