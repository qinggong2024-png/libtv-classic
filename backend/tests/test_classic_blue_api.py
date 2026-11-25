from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import classic_blue_api as blue  # noqa: E402
import classic_green_api as green  # noqa: E402
import classic_lan  # noqa: E402


def test_blue_pin_is_separate_and_rejects_invalid_source(tmp_path):
    root = tmp_path / "main"
    root.mkdir()
    release = tmp_path / "release"
    release.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    assert blue.pin_root(runtime, root) == root
    blue.write_pin(runtime, release, "a" * 64)
    assert blue.pin_root(runtime, root) == release
    blue.write_pin(runtime, root, "a" * 64)
    with pytest.raises(RuntimeError, match="identity"):
        blue.read_pin(runtime, root)


def test_replacement_checks_supervisor_before_any_process_change(tmp_path, monkeypatch):
    monkeypatch.setattr(blue, "supervisor_supports_blue", lambda _runtime: False)
    with pytest.raises(RuntimeError, match="supervisor"):
        blue.guard_replacement(tmp_path, tmp_path, "a" * 64, 15)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("online", [False, True])
def test_blue_replace_only_replaces_inactive_api_with_pinned_code(tmp_path, monkeypatch, online):
    root = tmp_path / "main"
    runtime = root / "var" / "classic-lan" / "runtime"
    runtime.mkdir(parents=True)
    release = tmp_path / "release"
    release.mkdir()
    state_path = runtime / "stack.json"
    state = {
        "apiPort": 8002,
        "webPort": 5176,
        "lanIp": "10.12.34.56",
        "processes": [
            {"name": "api", "pid": 11},
            {"name": "runner", "pid": 22},
            {"name": "cutflow", "pid": 33},
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(classic_lan, "ROOT", root)
    monkeypatch.setattr(classic_lan, "STATE", state_path)
    monkeypatch.setattr(green, "validated_release", lambda p, r: p)
    active = {"sourceRevision": "b" * 64, "configRevision": "cfg", "schemaRevision": "schema"}
    previous = {"sourceRevision": "c" * 64}
    if online:
        import classic_online_standby as standby

        monkeypatch.setattr(blue, "supervisor_supports_blue", lambda _: True)
        monkeypatch.setattr(standby, "guard", lambda *_: (active, previous))
        monkeypatch.setattr(standby, "verify_prepared", lambda *_: None)
        def unexpected_strict(*_):
            pytest.fail("online standby preparation incorrectly invoked strict gate")
        monkeypatch.setattr(blue, "guard_replacement", unexpected_strict)
    else:
        monkeypatch.setattr(blue, "guard_replacement", lambda *_: (active, previous))
    monkeypatch.setattr(green, "web_route", lambda: {"activeApiPort": 8003})
    calls = []

    class Process:
        def cmdline(self):
            return ["python", "-m", "uvicorn", "--port", "8002"]

        def terminate(self):
            calls.append("terminate-api")

        def wait(self, timeout):
            calls.append("api-exited")

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def connect_ex(self, address):
            assert address == ("127.0.0.1", 8002)
            return 1

    monkeypatch.setattr(blue.socket, "socket", Socket)
    monkeypatch.setattr(classic_lan, "owned_process", lambda r: Process())
    monkeypatch.setattr(
        classic_lan,
        "launch_commands",
        lambda *_: [("api", ["pinned-api"]), ("runner", ["runner"]), ("cutflow", ["cutflow"])],
    )
    monkeypatch.setattr(classic_lan, "lan_environment", lambda *_: {})

    def spawn(name, command, env):
        assert name == "api" and command == ["pinned-api"]
        assert blue.pin_root(runtime, root) == release
        calls.append("spawn-api")
        return {"name": "api", "pid": 44, "sourceRevision": "a" * 64}

    monkeypatch.setattr(classic_lan, "spawn_process", spawn)
    monkeypatch.setattr(
        classic_lan,
        "save_state",
        lambda value: state_path.write_text(json.dumps(value), encoding="utf-8"),
    )
    monkeypatch.setattr(green, "json_get", lambda *_: {**active, "sourceRevision": "a" * 64})
    dry = blue.replace_blue(
        release, "a" * 64, expected_active="b" * 64, baseline_unknown=15,
        online_prepare=online,
    )
    assert dry["action"] == "dry-run" and calls == []
    result = blue.replace_blue(
        release, "a" * 64, expected_active="b" * 64, baseline_unknown=15, apply=True,
        online_prepare=online,
    )
    assert result["action"] == "standby-prepared"
    assert calls == ["terminate-api", "api-exited", "spawn-api"]
    current = json.loads(state_path.read_text())
    assert current["processes"][1:] == state["processes"][1:]
    assert not (runtime / "api-routing.lock").exists()
