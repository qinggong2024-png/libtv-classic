import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classic_autostart  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_supervisor(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(classic_autostart, "RUNTIME", runtime)
    monkeypatch.setattr(classic_autostart, "STOP_FILE", runtime / "manual-stop")
    monkeypatch.setattr(classic_autostart, "SUPERVISOR", runtime / "supervisor.json")
    monkeypatch.setattr(classic_autostart.classic_lan, "STATE", runtime / "stack.json")


def test_startup_environment_retains_node_without_inheriting_secrets(tmp_path, monkeypatch):
    node = tmp_path / "Node with spaces" / "node.exe"
    node.parent.mkdir()
    node.touch()
    config = tmp_path / "autostart.json"
    config.write_text(json.dumps({"node": str(node)}), encoding="utf-8")
    monkeypatch.setattr(classic_autostart, "CONFIG", config)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-secret")
    monkeypatch.setenv("SESSION_SECRET", "unrelated-session")
    env = classic_autostart.startup_environment()
    path = next(value for key, value in env.items() if key.lower() == "path")
    assert path.startswith(str(node.parent))
    assert "OPENAI_API_KEY" not in env
    assert "SESSION_SECRET" not in env
    assert env["PAID_GENERATION_ENABLED"] == "false"


def test_network_not_ready_is_retried_until_success(monkeypatch):
    results = iter(
        [
            SimpleNamespace(returncode=1, stdout="LAN not ready"),
            SimpleNamespace(returncode=0, stdout="Classic LAN ready"),
        ]
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return next(results)

    monkeypatch.setattr(classic_autostart.subprocess, "run", run)
    monkeypatch.setattr(classic_autostart.time, "sleep", lambda _: None)
    assert classic_autostart.start_with_retry({"PATH": "test"})
    assert len(calls) == 2
    assert calls[0][0][-1] == "start"
    assert calls[0][1]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_retry_stops_at_deadline(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(classic_autostart.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        classic_autostart.time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay)
    )
    monkeypatch.setattr(
        classic_autostart.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="Not ready"),
    )
    assert not classic_autostart.start_with_retry({}, timeout_seconds=1)


def test_manual_stop_prevents_startup_retry(monkeypatch):
    classic_autostart.STOP_FILE.touch()
    monkeypatch.setattr(
        classic_autostart.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("Manual stop must prevent launching"),
    )
    assert not classic_autostart.start_with_retry({})


def record_stack():
    state = {
        "apiPort": 8002, "webPort": 5176, "lanIp": "10.12.34.56",
        "generationAuthorized": False,
        "processes": [
            {"name": "api", "pid": 100, "created": 1},
            {"name": "web", "pid": 200, "created": 1},
        ],
    }
    classic_autostart.classic_lan.STATE.write_text(json.dumps(state), encoding="utf-8")
    return state


def test_recovers_only_dead_child_and_saves_replacement(monkeypatch):
    state = record_stack()
    lan = classic_autostart.classic_lan
    monkeypatch.setattr(lan, "generation_authorized", lambda: False)
    monkeypatch.setattr(
        lan, "launch_commands",
        lambda *args: [("api", ["api-command"]), ("web", ["web-command"])],
    )
    monkeypatch.setattr(lan, "lan_environment", lambda *args: {"isolated": "yes"})
    monkeypatch.setattr(classic_autostart, "owned_process", lambda p: p["name"] == "api")
    spawned = []

    def spawn(name, command, env):
        spawned.append((name, command, env))
        return {"name": name, "pid": 300, "created": 2}

    monkeypatch.setattr(lan, "spawn_process", spawn)
    assert classic_autostart.recover_processes() == ["web"]
    assert spawned == [("web", ["web-command"], {"isolated": "yes"})]
    saved = json.loads(lan.STATE.read_text())
    assert saved["processes"][0] == state["processes"][0]
    assert saved["processes"][1]["pid"] == 300


def test_recovery_respects_manual_stop(monkeypatch):
    record_stack()
    classic_autostart.STOP_FILE.touch()
    lan = classic_autostart.classic_lan
    monkeypatch.setattr(lan, "generation_authorized", lambda: False)
    monkeypatch.setattr(
        lan, "launch_commands", lambda *args: [("api", ["api"]), ("web", ["web"])],
    )
    monkeypatch.setattr(lan, "lan_environment", lambda *args: {})
    monkeypatch.setattr(lan, "spawn_process", lambda *args: pytest.fail("Stopped manually"))
    assert classic_autostart.recover_processes() == []


def test_recovery_refuses_changed_generation_policy(monkeypatch):
    record_stack()
    monkeypatch.setattr(classic_autostart.classic_lan, "generation_authorized", lambda: True)
    with pytest.raises(RuntimeError, match="Generation policy changed"):
        classic_autostart.recover_processes()


def test_wmi_launcher_passes_paths_as_data(monkeypatch):
    monkeypatch.setattr(classic_autostart, "is_elevated", lambda: False)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="12345\n")

    monkeypatch.setattr(classic_autostart.subprocess, "run", run)
    monkeypatch.setattr(classic_autostart, "independent_command", lambda: 'pythonw "A & B/test.py"')
    assert classic_autostart.launch_independent() == 12345
    request = json.loads(calls[0][1]["input"])
    assert request["command"] == 'pythonw "A & B/test.py"'
    assert request["command"] not in calls[0][0][-1]
    assert "Win32_Process" in calls[0][0][-1]


def test_elevated_manual_launch_uses_normal_interactive_token(monkeypatch):
    calls = []
    monkeypatch.setattr(classic_autostart, "is_elevated", lambda: True)
    monkeypatch.setattr(classic_autostart, "launch_unelevated", lambda: calls.append("limited"))
    monkeypatch.setattr(
        classic_autostart.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("Elevated WMI must not be used"),
    )
    assert classic_autostart.launch_independent() is None
    assert calls == ["limited"]


def test_limited_bootstrap_uses_one_shot_nonadmin_scheduler(monkeypatch):
    requests = []

    def run(command, **kwargs):
        requests.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="STARTED\n")

    monkeypatch.setattr(classic_autostart.subprocess, "run", run)
    classic_autostart.launch_unelevated()
    assert len(requests) == 1
    command, kwargs = requests[0]
    data = json.loads(kwargs["input"])
    assert data["executable"].endswith("pythonw.exe")
    assert "classic_autostart.py" in data["arguments"]
    assert data["arguments"].endswith("run")
    assert "Schedule.Service" in command[-1]
    assert "RunLevel = 0" in command[-1]
    assert "LogonType = 3" in command[-1]
    assert "DeleteTask" in command[-1]
    assert "Actions.Create(0)" in command[-1]
    assert "Triggers.Create" not in command[-1]


def test_limited_bootstrap_fails_closed(monkeypatch):
    monkeypatch.setattr(
        classic_autostart.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="error"),
    )
    with pytest.raises(RuntimeError, match="normal-user startup failed"):
        classic_autostart.launch_unelevated()


def test_independent_command_keeps_checkout_identifiable_for_pid_validation():
    command = classic_autostart.independent_command()
    normalized = command.lower().replace("\\", "/")
    assert classic_autostart.ROOT.as_posix().lower() in normalized
    assert "classic_autostart.py" in command
    assert command.endswith("watch")
    assert str(Path(sys._base_executable).with_name("pythonw.exe")) in command


def test_stop_waits_for_supervisor_before_stopping_children(monkeypatch):
    import classic_green_api

    monkeypatch.setattr(classic_green_api, "read_manifest", lambda: None)
    records = iter([{"pid": 1}, {}])
    monkeypatch.setattr(classic_autostart, "supervisor_record", lambda: next(records))
    monkeypatch.setattr(classic_autostart.time, "sleep", lambda _: None)
    calls = []
    monkeypatch.setattr(
        classic_autostart.subprocess, "run", lambda command, **kwargs: calls.append(command),
    )
    classic_autostart.stop()
    assert classic_autostart.STOP_FILE.exists()
    assert len(calls) == 1
    assert calls[0][-1] == "stop"


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("CLASSIC_TEST_WINDOWS_LAUNCH") != "1",
    reason="Explicit opt-in for Windows WMI process isolation verification",
)
def test_wmi_child_is_independent_and_survives_launcher_exit(tmp_path):
    import time

    probe = tmp_path / "probe.py"
    ready_file = tmp_path / "ready.json"
    survived = tmp_path / "survived"
    probe.write_text(
        "import json,site,sys,time\n"
        "from pathlib import Path\n"
        f"site.addsitedir({str(Path(sys.prefix) / 'Lib' / 'site-packages')!r})\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "import classic_autostart as m\n"
        f"Path({str(ready_file)!r}).write_text(json.dumps(m.isolation_status()))\n"
        "time.sleep(3)\n"
        f"Path({str(survived)!r}).touch()\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys._base_executable, str(probe)])
    launcher = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys,time; "
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
            "import classic_autostart as m; "
            f"m.independent_command=lambda: {command!r}; "
            "print(m.launch_independent(),flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 35
        while not ready_file.exists() and time.monotonic() < deadline:
            if launcher.poll() is not None:
                pytest.fail(launcher.communicate()[1])
            time.sleep(0.1)
        assert ready_file.exists(), "Independent probe did not start"
        assert json.loads(ready_file.read_text()) == {
            "inJob": False, "hasPackageIdentity": False,
        }
        launcher.terminate()
        launcher.wait(timeout=5)
        while not survived.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert survived.exists(), "Probe did not survive launcher termination"
    finally:
        if launcher.poll() is None:
            launcher.terminate()
            launcher.wait(timeout=5)
        launcher.stdout.close()
        launcher.stderr.close()


def test_supervisor_rejects_reused_pid(monkeypatch):
    classic_autostart.SUPERVISOR.write_text(
        json.dumps({"pid": 123, "created": 1}), encoding="utf-8",
    )
    monkeypatch.setattr(classic_autostart, "owned_process", lambda _: None)
    assert classic_autostart.supervisor_record() == {}


def test_watch_refuses_bound_process_without_recursive_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(classic_autostart, "ROOT", tmp_path)
    monkeypatch.setattr(
        classic_autostart, "isolation_status",
        lambda: {"inJob": True, "hasPackageIdentity": False},
    )
    monkeypatch.setattr(
        classic_autostart, "launch_independent",
        lambda: pytest.fail("A failed independent launch must not recurse"),
    )
    assert not classic_autostart.run(independent=True)


def test_signin_hands_off_to_independent_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(classic_autostart, "ROOT", tmp_path)
    monkeypatch.setattr(
        classic_autostart, "isolation_status",
        lambda: {"inJob": True, "hasPackageIdentity": False},
    )
    calls = []
    monkeypatch.setattr(classic_autostart, "launch_independent", lambda: calls.append("launch"))
    assert classic_autostart.run()
    assert calls == ["launch"]


def test_watch_refuses_elevation_even_without_job(tmp_path, monkeypatch):
    monkeypatch.setattr(classic_autostart, "ROOT", tmp_path)
    monkeypatch.setattr(
        classic_autostart, "isolation_status",
        lambda: {"inJob": False, "hasPackageIdentity": False},
    )
    monkeypatch.setattr(classic_autostart, "is_elevated", lambda: True)
    monkeypatch.setattr(
        classic_autostart, "launch_independent",
        lambda: pytest.fail("Elevated watch must never start another launch"),
    )
    assert not classic_autostart.run(independent=True)


def test_singleton_keeps_existing_supervisor_record(tmp_path, monkeypatch):
    import msvcrt
    monkeypatch.setattr(classic_autostart, "is_elevated", lambda: False)

    monkeypatch.setattr(classic_autostart, "ROOT", tmp_path)
    monkeypatch.setattr(
        classic_autostart, "isolation_status",
        lambda: {"inJob": False, "hasPackageIdentity": False},
    )
    classic_autostart.SUPERVISOR.write_text('{"pid": 123}', encoding="utf-8")

    def locked(*args):
        raise OSError("Already locked")

    monkeypatch.setattr(msvcrt, "locking", locked)
    assert classic_autostart.run(independent=True)
    assert json.loads(classic_autostart.SUPERVISOR.read_text())["pid"] == 123
