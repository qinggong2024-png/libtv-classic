"""Start and supervise the classic LAN site independently of the invoking app."""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import classic_lan
import psutil
from classic_lan import PYTHON, ROOT, protect_runtime
from classic_stack import child_environment, owned_process, ready

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LibTVClassicLAN-0e3d1c8"
RUNTIME = ROOT / "var" / "classic-lan" / "runtime"
CONFIG = RUNTIME / "autostart.json"
SCRIPT = ROOT / "scripts" / "classic_autostart.py"
SUPERVISOR = RUNTIME / "supervisor.json"
STOP_FILE = classic_lan.STOP_FILE
CHECK_INTERVAL_SECONDS = 30

# The WMI provider creates the process outside the caller's app package/job.
# JSON on stdin avoids interpreting checkout paths as PowerShell source.
WMI_LAUNCH = r"""
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    ShowWindow = [uint16]0
    CreateFlags = [uint32]16777224
}
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = [string]$request.command
    CurrentDirectory = [string]$request.directory
    ProcessStartupInformation = $startup
}
if ($result.ReturnValue -ne 0) { throw "Independent launch failed: $($result.ReturnValue)" }
$result.ProcessId
"""

# One-shot task bootstrap: no triggers, no second autostart mechanism. The
# regular-user process hands off to WMI and exits; WMI starts the supervisor
# outside both the calling app's Job and the Task Scheduler Job.
LIMITED_INTERACTIVE_LAUNCH = r"""
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder("\")
$name = 'ALLWEClassicUserLaunch-' + [guid]::NewGuid().ToString('N')
$task = $service.NewTask(0)
$task.RegistrationInfo.Description = 'Temporary ALLWE classic normal-user bootstrap; no triggers'
$task.Principal.UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$task.Principal.LogonType = 3  # TASK_LOGON_INTERACTIVE_TOKEN: must already be signed in
$task.Principal.RunLevel = 0   # TASK_RUNLEVEL_LUA: never use the elevated token
$task.Settings.Enabled = $true
$task.Settings.Hidden = $true
$task.Settings.AllowDemandStart = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.ExecutionTimeLimit = 'PT2M'
$action = $task.Actions.Create(0)  # TASK_ACTION_EXEC
$action.Path = [string]$request.executable
$action.Arguments = [string]$request.arguments
$action.WorkingDirectory = [string]$request.directory
$registered = $null
try {
    $registered = $folder.RegisterTaskDefinition($name, $task, 2, $null, $null, 3)
    $startedAfter = [DateTime]::Now.AddSeconds(-2)
    $null = $registered.Run($null)
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while ([DateTime]::UtcNow -lt $deadline) {
        # A cached IRunningTask.State can remain RUNNING after the instance exits.
        # Refresh IRegisteredTask and require both a new LastRunTime and READY.
        $registered = $folder.GetTask($name)
        if ([int]$registered.State -eq 3 -and $registered.LastRunTime -ge $startedAfter) {
            break
        }
        Start-Sleep -Milliseconds 150
    }
    if ([int]$registered.State -ne 3 -or $registered.LastRunTime -lt $startedAfter) {
        throw 'Temporary user bootstrap did not finish'
    }
    if ([int]$registered.LastTaskResult -ne 0) {
        throw 'Temporary user bootstrap exited unsuccessfully'
    }
    'STARTED'
} finally {
    if ($null -ne $registered) { $folder.DeleteTask($name, 0) }
}
"""


def is_elevated() -> bool:
    """Check Windows token elevation without requiring optional pywin32 packages."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        if not advapi.GetTokenInformation(
            token, 20, ctypes.byref(elevation),
            ctypes.sizeof(elevation), ctypes.byref(size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(elevation.value)
    finally:
        kernel.CloseHandle(token)


def launch_unelevated() -> None:
    """Ask the *existing signed-in user's* LUA token to launch the WMI handoff."""
    command = [str(PYTHON.with_name("pythonw.exe")), str(SCRIPT), "run"]
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         LIMITED_INTERACTIVE_LAUNCH],
        input=json.dumps({
            "executable": command[0],
            "arguments": subprocess.list2cmdline(command[1:]),
            "directory": str(ROOT),
        }),
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        timeout=55, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or "STARTED" not in result.stdout:
        raise RuntimeError(
            "Classic normal-user startup failed; inspect Windows Task Scheduler "
            f"(exit {result.returncode})."
        )


def isolation_status() -> dict:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.GetPackageFullName.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR,
    ]
    handle = kernel.GetCurrentProcess()
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(handle, None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    length = wintypes.UINT()
    result = kernel.GetPackageFullName(handle, ctypes.byref(length), None)
    if result not in (0, 122, 15700):
        raise OSError(result, "Cannot verify process package identity")
    return {"inJob": bool(in_job.value), "hasPackageIdentity": result != 15700}


def supervisor_record() -> dict:
    try:
        record = json.loads(SUPERVISOR.read_text(encoding="utf-8"))
        process = owned_process(record)
        if process and "classic_autostart.py" in " ".join(process.cmdline()):
            return record
    except (OSError, ValueError, psutil.Error):
        pass
    return {}


def launch_independent() -> int | None:
    # WMI preserves the invoking UAC token; when called from AgentDock's
    # elevated token, switch to the linked signed-in normal token first.
    if is_elevated():
        launch_unelevated()
        return None
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WMI_LAUNCH],
        input=json.dumps({"command": independent_command(), "directory": str(ROOT)}),
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(f"Independent Windows launch failed: {result.stderr.strip()}")
    return int(result.stdout.strip())


def start() -> None:
    protect_runtime()
    # Manual startup also works before sign-in autostart has been installed.
    if not CONFIG.exists():
        node = shutil.which("node")
        if not node:
            raise SystemExit("Node.js is required.")
        CONFIG.write_text(json.dumps({"node": node}), encoding="utf-8")
    if STOP_FILE.exists() and supervisor_record():
        raise SystemExit("Classic supervisor is stopping; retry after it exits.")
    if not supervisor_record():
        launch_independent()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        record = supervisor_record()
        if record and ready(5176, "/api/v1/health/ready"):
            state = json.loads(classic_lan.STATE.read_text(encoding="utf-8"))
            if (
                all(owned_process(p) for p in state.get("processes", []))
                and (not classic_lan.generation_authorized() or classic_lan.runner_ready(state))
            ):
                print("Classic LAN supervised and ready: http://192.168.30.126:5176/")
                return
        time.sleep(0.5)
    raise SystemExit("Classic startup is not ready; inspect var/classic-lan/logs/autostart.log.")


def stop() -> None:
    # An active green route must first be explicitly switched back while
    # preserving the original request identity; never stop its supervisor.
    from classic_green_api import read_manifest, web_route

    if read_manifest() and web_route()["activeApiPort"] == 8003:
        raise SystemExit("Active green API must be routed back before stopping classic")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STOP_FILE.touch()
    deadline = time.monotonic() + 100
    while supervisor_record():
        if time.monotonic() >= deadline:
            raise SystemExit("Supervisor has not stopped; refusing a concurrent stack shutdown.")
        time.sleep(0.2)
    subprocess.run(
        [str(PYTHON), str(ROOT / "scripts" / "classic_lan.py"), "stop"],
        cwd=ROOT, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def startup_command() -> str:
    return subprocess.list2cmdline([str(PYTHON.with_name("pythonw.exe")), str(SCRIPT), "run"])


def independent_command() -> str:
    # uv's venv redirector creates a job for its child. Launch the real interpreter
    # with this venv's dependencies so the supervisor itself can be outside jobs.
    executable = Path(sys._base_executable).with_name("pythonw.exe")
    source = (
        "import runpy,site,sys;from pathlib import Path;"
        "root=Path(sys.argv.pop(1));"
        "site.addsitedir(str(root/'.venv'/'Lib'/'site-packages'));"
        "sys.path.insert(0,str(root/'scripts'));"
        "runpy.run_path(str(root/'scripts'/'classic_autostart.py'),run_name='__main__')"
    )
    return subprocess.list2cmdline([str(executable), "-c", source, str(ROOT), "watch"])


def registered_command() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except FileNotFoundError:
        return None


def install() -> None:
    import winreg

    current = registered_command()
    if current is not None and current != startup_command():
        raise SystemExit("Startup entry belongs to another checkout; refusing to replace it.")
    node = shutil.which("node")
    if not node or not PYTHON.with_name("pythonw.exe").is_file():
        raise SystemExit("Node.js and this instance's pythonw.exe are required.")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit("Build the independent classic frontend first.")
    protect_runtime()
    CONFIG.write_text(json.dumps({"node": node}, indent=2), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
    if registered_command() != startup_command():
        raise SystemExit("Could not verify the Windows sign-in startup entry.")
    print("Enabled: classic LAN starts silently after this Windows account signs in.")


def remove() -> None:
    import winreg

    current = registered_command()
    if current is not None and current != startup_command():
        raise SystemExit("Startup entry belongs to another checkout; refusing to remove it.")
    if current is not None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    print("Disabled: future Windows sign-ins will not start the classic LAN site.")


def startup_environment() -> dict[str, str]:
    env = child_environment(8002, 5176)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    node = Path(config["node"])
    if not node.is_file():
        raise FileNotFoundError("The configured Node.js executable is unavailable.")
    path_key = next((key for key in env if key.lower() == "path"), "PATH")
    env[path_key] = str(node.parent) + os.pathsep + env.get(path_key, "")
    return env


def start_with_retry(env: dict[str, str], timeout_seconds: float = 300) -> bool:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    while time.monotonic() < deadline and not STOP_FILE.exists():
        attempt += 1
        try:
            result = subprocess.run(
                [str(PYTHON), str(ROOT / "scripts" / "classic_lan.py"), "start"],
                cwd=ROOT,
                env={**env, "CLASSIC_MANAGED_GREEN_RECOVERY": "1"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(90, max(1, deadline - time.monotonic())),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logging.info(
                "Attempt %s (exit %s): %s", attempt, result.returncode, result.stdout.strip()
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired) as error:
            logging.warning("Attempt %s: %s", attempt, error)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(10, remaining))
    logging.error("Automatic startup timed out; use start-classic-lan.cmd and inspect the logs.")
    return False


def recover_processes(node: str | None = None) -> list[str]:
    state = json.loads(classic_lan.STATE.read_text(encoding="utf-8"))
    if state.get("generationAuthorized", False) != classic_lan.generation_authorized():
        raise RuntimeError("Generation policy changed; use the stop/start scripts to apply it.")
    commands = dict(
        classic_lan.launch_commands(state["apiPort"], state["webPort"], state["lanIp"], node)
    )
    if set(commands) != {p["name"] for p in state["processes"]}:
        raise RuntimeError("Recorded processes differ from configuration; restart manually.")
    env = classic_lan.lan_environment(state["apiPort"], state["webPort"], state["lanIp"])
    recovered = []
    for index, record in enumerate(state["processes"]):
        if STOP_FILE.exists():
            break
        if owned_process(record):
            continue
        if record["name"] in {"runner", "cutflow"}:
            from classic_worker_handoff import restart_held

            if restart_held(record["name"]):
                logging.warning("Worker restart held for safe handoff: %s", record["name"])
                continue
        logging.error("Process exited: name=%s pid=%s", record["name"], record["pid"])
        replacement = classic_lan.spawn_process(record["name"], commands[record["name"]], env)
        state["processes"][index] = replacement
        classic_lan.save_state(state)
        logging.info("Process recovered: name=%s pid=%s", replacement["name"], replacement["pid"])
        recovered.append(record["name"])
    # The green API has an independent PID + source manifest and never owns a
    # Runner or CUT worker. Only resurrect the SAME pinned release if it is
    # already selected by the installed 5176 atomic API route.
    from classic_green_api import maybe_recover_green

    if not STOP_FILE.exists() and maybe_recover_green():
        logging.warning("Recovered active green API without replaying any HTTP request")
        recovered.append("api-green")
    return recovered


def supervise(env: dict[str, str]) -> bool:
    if not start_with_retry(env):
        return False
    path = next((value for key, value in env.items() if key.lower() == "path"), "")
    node = shutil.which("node", path=path)
    if not node:
        raise FileNotFoundError("The configured Node.js executable is unavailable.")
    logging.info(
        "Supervision active; checking child processes every %s seconds.", CHECK_INTERVAL_SECONDS,
    )
    delay = CHECK_INTERVAL_SECONDS
    unhealthy = False
    while not STOP_FILE.exists():
        try:
            recovered = recover_processes(node)
            delay = min(delay * 2, 60) if recovered else CHECK_INTERVAL_SECONDS
            healthy = ready(5176, "/api/v1/health/ready")
            if not healthy and not unhealthy:
                logging.warning("HTTP readiness failed; preserving live worker processes.")
            elif healthy and unhealthy:
                logging.info("HTTP readiness recovered.")
            unhealthy = not healthy
        except Exception:
            logging.exception("Supervision check failed; retrying without stopping live processes.")
            delay = min(delay * 2, 60)
        # Keep manual stop responsive even during crash-loop backoff.
        for _ in range(delay * 2):
            if STOP_FILE.exists():
                break
            time.sleep(0.5)
    logging.info("Manual stop requested; supervision stopped.")
    return True


def run(independent: bool = False) -> bool:
    import msvcrt

    logs = ROOT / "var" / "classic-lan" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[RotatingFileHandler(
            logs / "autostart.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        )],
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    isolation = isolation_status()
    elevated = is_elevated()
    if isolation["inJob"] or isolation["hasPackageIdentity"] or elevated:
        if independent:
            logging.error(
                "Independent process isolation failed: inJob=%s package=%s elevated=%s",
                isolation["inJob"], isolation["hasPackageIdentity"], elevated,
            )
            return False
        logging.info("Handing startup to the independent Windows process provider.")
        launch_independent()
        return True
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "autostart.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            logging.info("Another automatic startup is already in progress.")
            return True
        try:
            STOP_FILE.unlink(missing_ok=True)
            process = psutil.Process()
            record = {
                "pid": process.pid, "created": process.create_time(),
                "parentPid": process.ppid(), **isolation,
                "checkIntervalSeconds": CHECK_INTERVAL_SECONDS,
            }
            SUPERVISOR.write_text(json.dumps(record, indent=2), encoding="utf-8")
            logging.info("Independent supervisor started: %s", record)
            return supervise(startup_environment())
        except Exception:
            logging.exception("Automatic startup could not complete.")
            return False
        finally:
            SUPERVISOR.unlink(missing_ok=True)
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["install", "remove", "status", "run", "watch", "start", "stop"],
    )
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows only.")
    if args.action == "install":
        install()
    elif args.action == "remove":
        remove()
    elif args.action == "start":
        start()
    elif args.action == "stop":
        stop()
    elif args.action == "status":
        current = registered_command()
        print(json.dumps({
            "enabled": current == startup_command(), "command": current,
            "supervisor": supervisor_record(), "manualStop": STOP_FILE.exists(),
        }, indent=2))
    elif not run(independent=args.action == "watch"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
