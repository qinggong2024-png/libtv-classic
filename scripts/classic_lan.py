"""Serve the classic production build on a LAN with its own session login."""

import argparse
import asyncio
import csv
import ipaddress
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
from classic_cutflow import cutflow_environment
from classic_stack import REVISION, child_environment, owned_process, ready, stop_stack

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / "var" / "classic-lan"
STATE = STORAGE / "runtime" / "stack.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SECRET_FILE = STORAGE / "runtime" / "session-secret.txt"
LOGIN_FILE = STORAGE / "runtime" / "initial-admin.json"
STOP_FILE = STORAGE / "runtime" / "manual-stop"


def generation_authorized() -> bool:
    """Only this instance's explicit local opt-in can enable real submissions."""
    path = STORAGE / "runtime" / "generation-policy.json"
    if not path.exists():
        return False
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(policy, dict)
            or type(policy.get("version")) is not int
            or policy.get("version") != 1
            or type(policy.get("paidGenerationAuthorized")) is not bool
        ):
            raise ValueError
    except (OSError, ValueError) as error:
        raise SystemExit("Invalid classic generation policy; refusing to start.") from error
    return policy["paidGenerationAuthorized"]


def configure_generation(enabled: bool, state: dict) -> None:
    if any(owned_process(process) for process in state.get("processes", [])):
        raise SystemExit("Stop the classic LAN instance before changing generation authorization.")
    protect_runtime()
    path = STORAGE / "runtime" / "generation-policy.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "paidGenerationAuthorized": enabled}, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    print(f"Classic API generation {'authorized' if enabled else 'disabled'} for the next start.")


def runner_ready(state: dict) -> bool:
    runner = next((p for p in state.get("processes", []) if p["name"] == "runner"), None)
    if not runner or not owned_process(runner):
        return False
    try:
        instance = (STORAGE / "runner" / "service-instance-id").read_text(encoding="ascii").strip()
        uri = (STORAGE / "classic.db").resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.2) as database:
            row = database.execute(
                "SELECT last_heartbeat_at FROM runner_host WHERE service_instance_id = ?",
                (instance,),
            ).fetchone()
        if not row or not row[0]:
            return False
        heartbeat = datetime.fromisoformat(row[0]).replace(tzinfo=UTC).timestamp()
        return heartbeat >= runner["created"] and time.time() - heartbeat < 30
    except (OSError, ValueError, sqlite3.Error):
        return False


def supervised_green_recovery_pending() -> bool:
    """Permit the independent monitor to recover a pinned green after reboot.

    Never report the site fully healthy here: the 5176 HTTP health endpoint
    remains 502 until the SAME green API is actually restored. A manual
    classic_lan start does not get this degraded-supervised exception.
    """
    if os.environ.get("CLASSIC_MANAGED_GREEN_RECOVERY") != "1":
        return False
    try:
        from classic_green_api import green_process, read_manifest, web_route

        manifest = read_manifest()
        return bool(
            manifest
            and green_process(manifest) is None
            and web_route().get("activeApiPort") == 8003
        )
    except Exception:
        return False


def protect_runtime() -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        STATE.parent.chmod(0o700)
        return
    identity = subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sid = next(csv.reader(identity.strip().splitlines()))[1]
    subprocess.run(
        [
            "icacls",
            str(STATE.parent),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def lan_environment(api_port: int, web_port: int, lan_ip: str) -> dict[str, str]:
    env = child_environment(api_port, web_port)
    authorized = generation_authorized()
    env.update(
        {
            # Use the existing LAN HTTP/session contract; secure-cookie production
            # settings remain unchanged and require HTTPS in the historical app.
            "APP_ENV": "development",
            "DEPLOYMENT_ID": "libtv-classic-lan-0e3d1c8",
            "APP_PUBLIC_ORIGINS": f"http://{lan_ip}:{web_port},http://127.0.0.1:{web_port},"
            f"http://localhost:{web_port}",
            "DATABASE_URL": f"sqlite+aiosqlite:///{STORAGE.as_posix()}/classic.db",
            "OBJECT_STORE_PREFIX": "classic-lan-0e3d1c8",
            "ARTIFACT_ROOT": str(STORAGE / "artifacts"),
            "RUNNER_ROOT": str(STORAGE / "runner"),
            # Existing tasks continue during a maintenance intake freeze.
            # The classic API checks this file on each *new* video create.
            "CLASSIC_NEW_VIDEO_PAUSE_FILE": str(
                STORAGE / "runtime" / "pause-new-video-tasks.flag"
            ),
            "CLASSIC_API_ROUTE_FILE": str(STORAGE / "runtime" / "api-routing.json"),
            "CLASSIC_WORKER_LEASE_DIR": str(STORAGE / "runtime"),
            "LIBTV_CONFIG_DIR": str(STORAGE / "runtime" / "libtv"),
            "SHARED_AUTHENTICATION_ENABLED": "true",
            "SESSION_SECRET": SECRET_FILE.read_text(encoding="utf-8").strip(),
            "SESSION_COOKIE_NAME": "libtv_classic_lan_session",
            "CSRF_COOKIE_NAME": "libtv_classic_lan_csrf",
            # The real AI adapter reads the separately encrypted admin credentials.
            # This switch does not enable any paid video provider or submissions.
            "AI_ADAPTER_MODE": "openai",
            "OPENAI_MODEL": "gpt-6.2-sol",
            "OPENAI_REASONING_EFFORT": "medium",
            # ALL ACCOUNTS: new automatic jobs use Sol's simple first-frame OOTD.
            # No role or account rollout filter. Revert this toggle to roll back.
            "AI_OOTD_V1_ENABLED": "true",
            # Safe per-instance rollback: create var/classic-lan/runtime/disable-dynamic-ootd.flag
            # and restart only the classic API; legacy cross-scene Sol prompts resume.
            "ALLWE_OOTD_DYNAMIC_ENABLED": "0" if (
                STORAGE / "runtime" / "disable-dynamic-ootd.flag"
            ).exists() else "1",
            # Optional private prompt audit; off unless the explicit rollout flag exists.
            # Only the classic Runner needs this setting, not the public website.
            "ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED": "1" if (
                STORAGE / "runtime" / "enable-ootd-wire-audit.flag"
            ).exists() else "0",
            # Kling 3.0 Omni OOTD Sol -> camera-language Lite.
            # Disable instantly for new prompts by creating the marker
            # file below; the running Runner/API check it for each prompt.
            "ALLWE_KLING_TRANSLATOR_LITE_ENABLED": "1",
            "ALLWE_KLING_TRANSLATOR_LITE_DISABLE_FILE": str(
                STORAGE / "runtime" / "disable-kling-translator-lite.flag"
            ),
            # USP/V3.4 and prior pacing are OFF for new automatic jobs.
            "AI_CREATIVE_V25_ENABLED": "false",
            "FASTCUT_UGC_PACING_V1_ENABLED": "false",
            "AI_DIRECTOR_V34_ENABLED": "false",
            # Provider credentials remain in this instance's existing encrypted files.
            # Never inherit an enable switch or credentials from the calling shell.
            "LIBTV_EXECUTION_MODE": "live" if authorized else "dry-run",
            "PAID_GENERATION_ENABLED": str(authorized).lower(),
            "PAID_GENERATION_CONFIRMATION": "I_ACCEPT_LIBTV_POINT_CHARGES" if authorized else "",
            # Accepted cloud jobs are limited to 20; excess work stays in local FIFO.
            "KLING_API_MAX_ACTIVE_CLOUD_TASKS": "20",
            # 5176-only rollout: no increase to cloud slots or paid generation count.
            # Revert this switch to false to restore the previous Runner flow.
            "CLASSIC_THROUGHPUT_S1S3_ENABLED": "true",
            # Canary uses two downloads, below the validated maximum of four.
            "DOWNLOAD_CONCURRENCY": "2",
            # Increase concurrent Sol planning from two to three; prompts unchanged.
            "CLASSIC_AUTO_PLAN_CONCURRENCY": "3",
            "AUTO_RUN_FAKE_COMMANDS": "false" if authorized else "true",
        }
    )
    for key, name in [
        ("ADMIN_CREDENTIALS_FILE", "admin-credentials.json"),
        ("KLING_ADMIN_CONFIG_FILE", "kling-config.json"),
        ("BACH_ADMIN_CONFIG_FILE", "bach-config.json"),
        ("WAVESPEED_ADMIN_CONFIG_FILE", "wavespeed-config.json"),
    ]:
        env[key] = str(STORAGE / "runtime" / name)
    env.update(cutflow_environment(STORAGE))
    # Let each process fingerprint the source it actually loads, rather than
    # reporting the original extraction commit inherited from child_environment.
    env.pop("DEPLOYMENT_SOURCE_REVISION", None)
    return env


async def initialize_admin() -> None:
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from libtv_agent.config import get_settings
    from libtv_agent.db import Database
    from libtv_agent.domain.enums import UserRole
    from libtv_agent.domain.models import User
    from libtv_agent.services.auth import PasswordService
    from libtv_agent.services.prompt_presets import add_default_prompt_presets
    from sqlalchemy import select

    credentials = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
    database = Database(get_settings().database_url)
    try:
        async with database.session_factory() as session:
            existing = await session.scalar(
                select(User).where(User.username == credentials["username"])
            )
            if existing is None:
                user = User(
                    username=credentials["username"],
                    password_hash=PasswordService().hash(credentials["password"]),
                    role=UserRole.ADMIN,
                )
                session.add(user)
                await session.flush()
                add_default_prompt_presets(session, user.id)
                await session.commit()
                print("Independent classic administrator created.")
    finally:
        await database.dispose()


def launch_commands(
    api_port: int, web_port: int, lan_ip: str, node: str | None = None,
) -> list[tuple[str, list[str]]]:
    node = node or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required.")
    commands = [
        (
            "api",
            [
                str(PYTHON), "-m", "uvicorn", "libtv_agent.app:create_app", "--factory",
                "--app-dir", str(ROOT / "backend" / "src"), "--host", "127.0.0.1",
                "--port", str(api_port),
            ],
        ),
        (
            "web",
            [
                node, str(ROOT / "scripts" / "classic_web_server.mjs"),
                "--root", str(ROOT / "frontend" / "dist"), "--host", lan_ip,
                "--port", str(web_port), "--api-port", str(api_port),
                "--alternate-api-port", str(api_port + 1),
                "--api-route-file", str(STORAGE / "runtime" / "api-routing.json"),
            ],
        ),
    ]
    if generation_authorized():
        commands.append(("runner", [str(PYTHON), str(ROOT / "scripts" / "classic_runner.py")]))
    if cutflow_environment(STORAGE).get("CUTFLOW_HANDOFF_ENABLED") == "true":
        commands.append(
            ("cutflow", [str(PYTHON), str(ROOT / "scripts" / "classic_cutflow.py"), "worker"])
        )
    return commands


def spawn_process(name: str, command: list[str], env: dict[str, str]) -> dict:
    logs = STORAGE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f"{name}.log").open("ab") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return {
        "name": name,
        "pid": process.pid,
        "created": psutil.Process(process.pid).create_time(),
    }


def save_state(state: dict) -> None:
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["start", "stop", "status", "bootstrap", "enable-generation", "disable-generation"],
        default="start",
        nargs="?",
    )
    parser.add_argument("--lan-ip", default="192.168.30.126")
    parser.add_argument("--web-port", type=int, default=5176)
    parser.add_argument("--api-port", type=int, default=8002)
    args = parser.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action == "stop":
        # Never orphan a green API that is still serving 5176.
        from classic_green_api import stop_green

        stop_green()
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.touch()
        stop_stack(state, STATE)
        print("Classic LAN instance stopped.")
        return
    if args.action in {"enable-generation", "disable-generation"}:
        configure_generation(args.action == "enable-generation", state)
        return
    if args.action == "status":
        print(
            json.dumps(
                {
                    **state,
                    "processesAlive": [bool(owned_process(p)) for p in state.get("processes", [])],
                    "generationAuthorized": generation_authorized(),
                    "runnerReady": runner_ready(state),
                },
                indent=2,
            )
        )
        return
    if args.action == "bootstrap":
        asyncio.run(initialize_admin())
        return
    address = ipaddress.ip_address(args.lan_ip)
    if address.version != 4 or not address.is_private or address.is_loopback:
        raise SystemExit("Use this computer's private LAN IPv4 address.")
    if args.web_port == args.api_port or not all(
        1024 <= p <= 65535 for p in [args.web_port, args.api_port]
    ):
        raise SystemExit("Choose two different unprivileged ports.")
    if state and state.get("processes") and all(owned_process(p) for p in state["processes"]):
        if (
            state["webPort"] != args.web_port
            or state["apiPort"] != args.api_port
            or state["lanIp"] != args.lan_ip
        ):
            raise SystemExit(
                "Stop the recorded classic LAN instance before changing its ports or IP."
            )
        if state.get("generationAuthorized", False) != generation_authorized():
            raise SystemExit("Restart the classic LAN instance to apply its generation policy.")
        if ready(args.web_port, "/api/v1/health/ready") and (
            not generation_authorized() or runner_ready(state)
        ):
            print(f"Classic LAN already ready: {state['url']}")
            return
        raise SystemExit("Classic LAN instance is unhealthy; stop it before retrying.")
    if state:
        stop_stack(state, STATE)
    if (ROOT / ".env").exists() or list((ROOT / "frontend").glob(".env*")):
        raise SystemExit("Keep the classic instance independent of .env overrides.")
    if not PYTHON.is_file() or not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit("Install Python dependencies and run npm run build in frontend first.")
    for host, port in [
        ("127.0.0.1", args.api_port),
        ("127.0.0.1", args.web_port),
        (args.lan_ip, args.web_port),
    ]:
        with socket.socket() as sock:
            sock.bind((host, port))
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required.")
    protect_runtime()
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    if not LOGIN_FILE.exists():
        LOGIN_FILE.write_text(
            json.dumps(
                {"username": "classic-admin", "password": secrets.token_urlsafe(18)}, indent=2
            ),
            encoding="utf-8",
        )
    env = lan_environment(args.api_port, args.web_port, args.lan_ip)
    database = STORAGE / "classic.db"
    if not database.exists():
        subprocess.run(
            [str(PYTHON), str(ROOT / "scripts" / "init_offline_db.py"), str(database)],
            cwd=ROOT,
            env=env,
            check=True,
        )
    subprocess.run(
        [str(PYTHON), str(Path(__file__).resolve()), "bootstrap"], cwd=ROOT, env=env, check=True
    )
    logs = STORAGE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    commands = launch_commands(args.api_port, args.web_port, args.lan_ip, node)
    authorized = generation_authorized()
    state = {
        "root": str(ROOT),
        "revision": REVISION,
        "webPort": args.web_port,
        "apiPort": args.api_port,
        "lanIp": args.lan_ip,
        "url": f"http://{args.lan_ip}:{args.web_port}/",
        "mode": "static production build; independent LAN session login; "
        + ("API generation authorized; runner enabled" if authorized else "generation disabled"),
        "generationAuthorized": authorized,
        "processes": [],
    }
    try:
        for name, command in commands:
            state["processes"].append(spawn_process(name, command, env))
        save_state(state)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if (
                ready(args.web_port)
                and (
                    ready(args.web_port, "/api/v1/health/ready")
                    or supervised_green_recovery_pending()
                )
                and (not authorized or runner_ready(state))
            ):
                if ready(args.web_port, "/api/v1/health/ready"):
                    print(f"Classic LAN ready: {state['url']}")
                else:
                    print(
                        "Classic LAN web started; pinned green API is still unavailable. "
                        "Supervised recovery pending; HTTP readiness remains unhealthy."
                    )
                print(f"Initial administrator credentials (local only): {LOGIN_FILE}")
                return
            if not all(owned_process(p) for p in state["processes"]):
                break
            time.sleep(0.3)
        raise RuntimeError(f"LAN startup failed; inspect {logs}")
    except BaseException:
        stop_stack(state, STATE)
        raise


if __name__ == "__main__":
    main()
