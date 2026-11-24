"""Strict, supervisor-coordinated immutable 8002 standby replacement.

The legacy supervisor must first be upgraded to the blue-slot lock contract.
The active 5176 route, Runner, CUT, DB and frontend are never changed here.
Use the existing guarded switch to activate a successfully prepared slot.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

PIN_NAME = "blue-api-pin.json"
SUPERVISOR_NAME = "supervisor.json"


def read_pin(runtime: Path, root: Path) -> dict | None:
    path = runtime / PIN_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(raw, dict) or set(raw) != {"version", "releaseRoot", "sourceRevision"}:
        raise RuntimeError("invalid blue source pin")
    if raw["version"] != 1 or not isinstance(raw["releaseRoot"], str):
        raise RuntimeError("invalid blue source pin")
    release = Path(raw["releaseRoot"]).resolve()
    revision = raw["sourceRevision"]
    if (
        release == root.resolve()
        or release.parent != root.resolve().parent
        or not isinstance(revision, str)
        or len(revision) != 64
        or any(c not in "0123456789abcdef" for c in revision)
    ):
        raise RuntimeError("invalid blue source identity")
    return raw


def pin_root(runtime: Path, root: Path) -> Path:
    pin = read_pin(runtime, root)
    return Path(pin["releaseRoot"]).resolve() if pin else root


def write_pin(runtime: Path, release: Path, revision: str) -> None:
    path = runtime / PIN_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"version": 1, "releaseRoot": str(release), "sourceRevision": revision}, indent=2
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def supervisor_supports_blue(runtime: Path) -> bool:
    try:
        record = json.loads((runtime / SUPERVISOR_NAME).read_text(encoding="utf-8"))
        process = psutil.Process(record["pid"])
        return (
            record.get("blueSlotContractVersion") == 1
            and abs(process.create_time() - record["created"]) < 0.1
            and process.is_running()
            and "classic_autostart.py" in " ".join(process.cmdline()).lower()
        )
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return False


@contextmanager
def route_operator_lock(runtime: Path):
    path = runtime / "api-routing.lock"
    with path.open("x", encoding="ascii") as handle:
        try:
            handle.write("blue-standby-replacement")
            handle.flush()
            yield
        finally:
            handle.close()
            path.unlink(missing_ok=True)


def guard_replacement(runtime: Path, root: Path, expected_active: str, baseline_unknown: int):
    from classic_api_drain import inspect
    from classic_green_api import DEPLOYMENT, json_get, web_route
    from classic_v2_canary_preflight import check

    if not supervisor_supports_blue(runtime):
        raise RuntimeError("running supervisor has not adopted the blue-slot contract")
    route = web_route()
    if route.get("activeApiPort") != 8003:
        raise RuntimeError("8002 must be the inactive standby slot")
    gate = json_get(8003, "/api/v1/system/classic-new-video-admission")
    if (
        gate.get("deploymentId") != DEPLOYMENT
        or gate.get("configured") is not True
        or gate.get("newVideoAdmissionsPaused") is not True
    ):
        raise RuntimeError("strict paused intake required")
    active = json_get(8003, "/api/v1/system/status")
    if (
        active.get("sourceRevision") != expected_active
        or not active.get("configRevision")
        or not active.get("schemaRevision")
    ):
        raise RuntimeError("active API identity changed")
    task_report = check(
        root / "var" / "classic-lan" / "classic.db", baseline_uncertain=baseline_unknown
    )
    if not task_report["quiescentForVersionSwitch"]:
        raise RuntimeError("strict task/payment/CUT release gate is blocked")
    previous = json_get(8002, "/api/v1/system/status")
    drain = inspect(8002, previous.get("sourceRevision", ""))
    if not drain["ready"]:
        raise RuntimeError("standby API local work has not drained")
    return active, previous


def replace_blue(
    release: Path,
    revision: str,
    *,
    expected_active: str,
    baseline_unknown: int,
    apply: bool = False,
):
    import classic_lan
    from classic_green_api import json_get, validated_release, web_route
    from classic_worker_lease import exclusive_classic_worker

    root, runtime = classic_lan.ROOT, classic_lan.STATE.parent
    release = validated_release(release, revision)
    if not apply:
        active, previous = guard_replacement(runtime, root, expected_active, baseline_unknown)
        return {
            "action": "dry-run",
            "slot": 8002,
            "sourceRevision": revision,
            "previousRevision": previous["sourceRevision"],
            "activeRevision": active["sourceRevision"],
        }
    with route_operator_lock(runtime), exclusive_classic_worker("api-blue", runtime):
        active, previous = guard_replacement(runtime, root, expected_active, baseline_unknown)
        state = json.loads(classic_lan.STATE.read_text(encoding="utf-8"))
        if state.get("apiPort") != 8002 or state.get("webPort") != 5176:
            raise RuntimeError("unexpected classic stack identity")
        matches = [
            index for index, record in enumerate(state["processes"]) if record.get("name") == "api"
        ]
        if len(matches) != 1:
            raise RuntimeError("cannot prove ownership of the blue slot")
        index = matches[0]
        record = state["processes"][index]
        process = classic_lan.owned_process(record)
        if not process:
            raise RuntimeError("blue process ownership unavailable; manual review required")
        cmd = process.cmdline()
        if "uvicorn" not in cmd or "--port" not in cmd or cmd[cmd.index("--port") + 1] != "8002":
            raise RuntimeError("recorded process is not the 8002 API")
        # Validate exact immutable source and the drain boundary before touching the process.
        release = validated_release(release, revision)
        guard_replacement(runtime, root, expected_active, baseline_unknown)
        write_pin(runtime, release, revision)
        process.terminate()
        process.wait(timeout=10)
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8002)) == 0:
                raise RuntimeError("blue port is still occupied; active green unchanged")
        commands = dict(classic_lan.launch_commands(8002, 5176, state["lanIp"]))
        env = classic_lan.lan_environment(8002, 5176, state["lanIp"])
        replacement = classic_lan.spawn_process("api", commands["api"], env)
        state["processes"][index] = replacement
        classic_lan.save_state(state)
        for _ in range(60):
            try:
                json_get(8002, "/api/v1/health/ready")
                info = json_get(8002, "/api/v1/system/status")
                if (
                    info.get("sourceRevision") == revision
                    and info.get("configRevision") == active["configRevision"]
                    and info.get("schemaRevision") == active["schemaRevision"]
                    and web_route().get("activeApiPort") == 8003
                ):
                    return {
                        "action": "standby-prepared",
                        "slot": 8002,
                        "sourceRevision": revision,
                        "previousRevision": previous["sourceRevision"],
                    }
            except Exception:
                pass
            time.sleep(0.3)
        raise RuntimeError("blue readiness/contract not verified; active green unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--expected-active-revision", required=True)
    parser.add_argument("--baseline-unknown", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.baseline_unknown < 0:
        parser.error("baseline must be nonnegative")
    print(
        json.dumps(
            replace_blue(
                args.release_root,
                args.expected_source_revision,
                expected_active=args.expected_active_revision,
                baseline_unknown=args.baseline_unknown,
                apply=args.apply,
            ),
            indent=2,
        )
    )
