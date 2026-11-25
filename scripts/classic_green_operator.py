"""Bounded one-shot Classic green operator outside temporary Windows Jobs.

Reuse the existing normal-user Scheduler bootstrap and WMI independent launcher.
No new daemon, no credentials in command lines, no routing or paid requests.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import classic_autostart as autostart
from classic_lan import PYTHON, ROOT, STORAGE

ACTIONS = {"start", "prepare-online", "restore-vacant-online", "recover", "prepare-blue"}


def needs_independent():
    if os.name != "nt":
        return False
    state = autostart.isolation_status()
    return state["inJob"] or state["hasPackageIdentity"] or autostart.is_elevated()


def result_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("invalid green operation identity")
    return STORAGE / "runtime" / ("green-operation-" + token + ".json")


def command(token, argv):
    result_path(token)
    if not argv or argv[0] not in ACTIONS:
        raise ValueError("unsupported independent green operation")
    # Same base-interpreter strategy as the existing independent supervisor.
    source = (
        "import runpy,site,sys;from pathlib import Path;"
        "root=Path(sys.argv.pop(1));"
        "site.addsitedir(str(root/'.venv'/'Lib'/'site-packages'));"
        "sys.path.insert(0,str(root/'scripts'));"
        "runpy.run_path(str(root/'scripts'/'classic_green_operator.py'),run_name='__main__')"
    )
    return [str(Path(sys._base_executable).with_name("pythonw.exe")),
            "-c", source, str(ROOT), "execute", token, *argv]


def powershell(script, payload, timeout):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(payload), text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("independent green launch failed; inspect operation before retry")
    return result.stdout.strip()


def launch_wmi(token, argv):
    if autostart.is_elevated():
        raise RuntimeError("green operator must use normal signed-in token")
    output = powershell(autostart.WMI_LAUNCH, {
        "command": subprocess.list2cmdline(command(token, argv)),
        "directory": str(ROOT),
    }, 30)
    if not output.isdigit():
        raise RuntimeError("independent green process identity unavailable")


def dispatch(argv):
    token = uuid.uuid4().hex
    path = result_path(token)
    if path.exists():
        raise RuntimeError("green operation identity already exists")
    if autostart.is_elevated():
        # Normal-user bootstrap exits after WMI creation, not after API startup.
        bootstrap = [str(PYTHON.with_name("pythonw.exe")),
                     str(ROOT / "scripts" / "classic_green_operator.py"),
                     "bootstrap", token, *argv]
        output = powershell(autostart.LIMITED_INTERACTIVE_LAUNCH, {
            "executable": bootstrap[0], "arguments": subprocess.list2cmdline(bootstrap[1:]),
            "directory": str(ROOT),
        }, 55)
        if "STARTED" not in output:
            raise RuntimeError("green bootstrap result unverified; do not retry blindly")
    else:
        launch_wmi(token, argv)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt.get("token") != token:
                raise RuntimeError("green operation receipt identity mismatch")
            if not receipt.get("ok"):
                raise RuntimeError("independent green operation failed: " + receipt.get("error", "unknown"))
            return receipt["result"]
        time.sleep(0.25)
    raise RuntimeError("green operation outcome unknown; inspect manifest and receipt before retry")


def execute(token, argv):
    path = result_path(token)
    if path.exists() or not argv or argv[0] not in ACTIONS:
        raise RuntimeError("invalid or previously completed green operation")
    if needs_independent():
        raise RuntimeError("green operation still inside temporary/elevated process")
    import classic_green_api
    output = io.StringIO()
    previous = sys.argv
    receipt = {"token": token, "ok": False}
    try:
        with contextlib.redirect_stdout(output):
            if argv[0] == "prepare-blue":
                import argparse
                from classic_blue_api import replace_blue
                parser = argparse.ArgumentParser()
                parser.add_argument("--release-root", type=Path, required=True)
                for flag in ("expected-source-revision", "expected-active-revision", "expected-standby-revision"):
                    parser.add_argument("--" + flag, required=True)
                parser.add_argument("--baseline-unknown", type=int, default=15)
                parser.add_argument("--apply", action="store_true", required=True)
                args = parser.parse_args(argv[1:])
                from classic_release_classifier import reviewed_pair
                if (args.baseline_unknown != 15
                        or not reviewed_pair(args.expected_active_revision, args.expected_source_revision)
                        or (args.expected_standby_revision != args.expected_active_revision
                            and not reviewed_pair(args.expected_active_revision, args.expected_standby_revision))):
                    raise RuntimeError("unreviewed blue slot preparation")
                print(json.dumps(replace_blue(args.release_root, args.expected_source_revision,
                    expected_active=args.expected_active_revision, expected_standby=(None if args.expected_standby_revision == args.expected_active_revision else args.expected_standby_revision),
                    baseline_unknown=args.baseline_unknown, online_prepare=True, apply=True)))
            else:
                sys.argv = [str(ROOT / "scripts" / "classic_green_api.py"), *argv]
                classic_green_api.main()
        receipt.update(ok=True, result=json.loads(output.getvalue()))
    except (Exception, SystemExit) as error:
        # No traceback, environment, or upstream response in operation receipts.
        receipt["error"] = type(error).__name__ + ": " + str(error)[:400]
    finally:
        sys.argv = previous
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return receipt["ok"]


if __name__ == "__main__":
    mode, token, *argv = sys.argv[1:]
    if mode == "bootstrap":
        launch_wmi(token, argv)
    elif mode == "execute":
        raise SystemExit(0 if execute(token, argv) else 1)
    else:
        raise SystemExit("invalid green operator mode")
