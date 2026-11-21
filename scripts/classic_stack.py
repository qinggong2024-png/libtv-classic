"""Run the extracted classic UI against its own zero-cost local API."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "var" / "classic" / "runtime" / "stack.json"
REVISION = "0e3d1c831efd4cd074654a03e36c472f15565cc3"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def child_environment(api_port: int, web_port: int) -> dict[str, str]:
    # Inherit OS essentials only, never another project's provider keys or DB URL.
    essentials = {
        "path",
        "systemroot",
        "windir",
        "comspec",
        "pathext",
        "temp",
        "tmp",
        "userprofile",
        "appdata",
        "localappdata",
        "programdata",
        "homedrive",
        "homepath",
    }
    env = {key: value for key, value in os.environ.items() if key.lower() in essentials}
    storage = ROOT / "var" / "classic"
    env.update(
        {
            "APP_ENV": "test",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(api_port),
            "APP_PUBLIC_ORIGINS": f"http://127.0.0.1:{web_port},http://localhost:{web_port}",
            "DEPLOYMENT_ID": "libtv-classic-0e3d1c8",
            "DEPLOYMENT_SOURCE_REVISION": REVISION,
            "DATABASE_URL": f"sqlite+aiosqlite:///{storage.as_posix()}/classic.db",
            "OBJECT_STORE_MODE": "local",
            "OBJECT_STORE_PREFIX": "classic-0e3d1c8",
            "ARTIFACT_ROOT": str(storage / "artifacts"),
            "RUNNER_ROOT": str(storage / "runner"),
            "LIBTV_CONFIG_DIR": str(storage / "runtime" / "libtv"),
            "ADMIN_CREDENTIALS_FILE": str(storage / "runtime" / "admin-credentials.json"),
            "KLING_ADMIN_CONFIG_FILE": str(storage / "runtime" / "kling-config.json"),
            "BACH_ADMIN_CONFIG_FILE": str(storage / "runtime" / "bach-config.json"),
            "WAVESPEED_ADMIN_CONFIG_FILE": str(storage / "runtime" / "wavespeed-config.json"),
            "SHARED_AUTHENTICATION_ENABLED": "false",
            "SESSION_COOKIE_NAME": "libtv_classic_session",
            "CSRF_COOKIE_NAME": "libtv_classic_csrf",
            "SESSION_COOKIE_SECURE": "false",
            "AI_ADAPTER_MODE": "fake",
            "LIBTV_ADAPTER_MODE": "fake",
            "LIBTV_EXECUTION_MODE": "dry-run",
            "PAID_GENERATION_ENABLED": "false",
            "PAID_GENERATION_CONFIRMATION": "",
            "PAID_SUBMISSIONS_PAUSED": "true",
            "KLING_CLI_ENABLED": "false",
            "KLING_API_ENABLED": "false",
            "KLING_ENABLE_PAID_INTEGRATION_TEST": "false",
            "KLING_API_ENABLE_PAID_INTEGRATION_TEST": "false",
            "WAVESPEED_ENABLED": "false",
            "BACH_ENABLED": "false",
            "FEEDBACK_LEARNING_LLM_ENABLED": "false",
            "VITE_API_PROXY": f"http://127.0.0.1:{api_port}",
            "VITE_HOST": "127.0.0.1",
            "PYTHONPATH": str(ROOT / "backend" / "src"),
            "PYTHONUTF8": "1",
        }
    )
    return env


def owned_process(record: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(record["pid"])
        if abs(process.create_time() - record["created"]) > 0.1:
            return None
        command = " ".join(process.cmdline()).lower().replace("\\", "/")
        if ROOT.as_posix().lower() not in command:
            return None
        return process
    except (psutil.Error, KeyError):
        return None


def ready(port: int, path: str = "/") -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def stop_stack(state: dict, state_file: Path = STATE) -> None:
    for record in state.get("processes", []):
        process = owned_process(record)
        if process:
            children = process.children(recursive=True)
            for target in [*reversed(children), process]:
                try:
                    target.terminate()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs([*children, process], timeout=5)
            for target in alive:
                try:
                    target.kill()
                except psutil.Error:
                    pass
    state_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"], nargs="?", default="start")
    parser.add_argument("--web-port", type=int, default=5176)
    parser.add_argument("--api-port", type=int, default=8002)
    args = parser.parse_args()
    if args.web_port == args.api_port or not all(
        1024 <= port <= 65535 for port in [args.web_port, args.api_port]
    ):
        raise SystemExit("Choose two different unprivileged ports.")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action == "stop":
        stop_stack(state)
        print("Classic instance stopped.")
        return
    if args.action == "status":
        print(
            json.dumps(
                {
                    **state,
                    "processesAlive": [bool(owned_process(p)) for p in state.get("processes", [])],
                },
                indent=2,
            )
        )
        return
    if state and all(owned_process(p) for p in state.get("processes", [])):
        if ready(state["webPort"], "/api/v1/health/ready"):
            print(f"Classic instance already ready: {state['url']}")
            return
        raise SystemExit("Recorded classic instance is not healthy; stop it before retrying.")
    if state:
        stop_stack(state)
    if (ROOT / ".env").exists() or list((ROOT / "frontend").glob(".env*")):
        raise SystemExit("Classic preview refuses local .env overrides; keep it isolated.")
    if not PYTHON.is_file() or not (ROOT / "frontend" / "node_modules").is_dir():
        raise SystemExit(
            "Install dependencies first: uv sync --frozen --extra dev; npm ci in frontend."
        )
    for port in [args.web_port, args.api_port]:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required.")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "var" / "classic" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = child_environment(args.api_port, args.web_port)
    database = ROOT / "var" / "classic" / "classic.db"
    if not database.exists():
        subprocess.run(
            [str(PYTHON), str(ROOT / "scripts" / "init_offline_db.py"), str(database)],
            cwd=ROOT,
            env=env,
            check=True,
        )
    commands = [
        (
            "api",
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "libtv_agent.app:create_app",
                "--factory",
                "--app-dir",
                str(ROOT / "backend" / "src"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
            ],
        ),
        (
            "web",
            [
                node,
                str(ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.web_port),
                "--strictPort",
            ],
        ),
    ]
    state = {
        "revision": REVISION,
        "root": str(ROOT),
        "webPort": args.web_port,
        "apiPort": args.api_port,
        "url": f"http://127.0.0.1:{args.web_port}/",
        "mode": "isolated classic preview; real generation disabled",
        "processes": [],
    }
    try:
        for name, command in commands:
            with (logs / f"{name}.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT / "frontend" if name == "web" else ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            state["processes"].append(
                {
                    "name": name,
                    "pid": process.pid,
                    "created": psutil.Process(process.pid).create_time(),
                }
            )
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if (
                ready(args.api_port, "/api/v1/health/ready")
                and ready(args.web_port, "/api/v1/health/ready")
                and ready(args.web_port)
            ):
                print(f"Classic instance ready: {state['url']}")
                print(f"Source {REVISION[:7]}; separate local database; real generation disabled.")
                return
            if not all(owned_process(p) for p in state["processes"]):
                break
            time.sleep(0.3)
        raise RuntimeError(f"Classic startup failed; inspect {logs}")
    except BaseException:
        stop_stack(state)
        raise


if __name__ == "__main__":
    main()
