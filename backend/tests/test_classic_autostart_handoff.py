"""Isolated supervisor tests: a handoff hold cannot cause duplicate workers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_autostart as supervisor  # noqa: E402
import classic_green_api as green  # noqa: E402
import classic_lan  # noqa: E402
import classic_worker_handoff as handoff  # noqa: E402


def test_dead_worker_with_handoff_marker_is_not_restarted(tmp_path, monkeypatch):
    state_path = tmp_path / "stack.json"
    state_path.write_text(json.dumps({
        "apiPort": 8002, "webPort": 5176, "lanIp": "192.168.30.126",
        "generationAuthorized": True,
        "processes": [
            {"name": "api", "pid": 1},
            {"name": "runner", "pid": 2},
            {"name": "cutflow", "pid": 3},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(classic_lan, "STATE", state_path)
    monkeypatch.setattr(classic_lan, "generation_authorized", lambda: True)
    monkeypatch.setattr(
        classic_lan, "launch_commands",
        lambda *_args: [("api", ["api"]), ("runner", ["runner"]), ("cutflow", ["cut"])],
    )
    monkeypatch.setattr(classic_lan, "lan_environment", lambda *_args: {})
    monkeypatch.setattr(
        supervisor, "owned_process",
        lambda record: object() if record["name"] != "runner" else None,
    )
    monkeypatch.setattr(supervisor, "STOP_FILE", tmp_path / "stop")
    monkeypatch.setattr(handoff, "restart_held", lambda kind: kind == "runner")
    monkeypatch.setattr(green, "maybe_recover_green", lambda: False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("supervisor attempted to start duplicate worker")

    monkeypatch.setattr(classic_lan, "spawn_process", forbidden)
    assert supervisor.recover_processes() == []
    assert json.loads(state_path.read_text(encoding="utf-8"))["processes"][1]["pid"] == 2
