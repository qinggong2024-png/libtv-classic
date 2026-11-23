"""No-provider tests for the first production hot-route release readiness."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_first_install_gate as gate  # noqa: E402


def fake_setup(tmp_path, monkeypatch):
    main = tmp_path / "main"
    release = tmp_path / "release"
    main.mkdir()
    release.mkdir()
    instance = main / "var" / "classic-lan" / "runtime"
    instance.mkdir(parents=True)
    (instance / "stack.json").write_text(
        json.dumps(
            {
                "root": str(main),
                "processes": [{"name": kind} for kind in ("api", "web", "runner", "cutflow")],
            }
        )
    )
    main_dirty = {"dirty": False}
    tasks = {
        "quiescentForVersionSwitch": True,
        "blockers": [],
        "counts": {"activeCutMulti": 0, "pendingPaidCommands": 0},
    }

    def git(root, *args):
        if args[0] == "status":
            return " M something.py" if root == main and main_dirty["dirty"] else ""
        if args[0] == "rev-parse":
            return "0" * 40 if root == main else "1" * 40
        raise AssertionError(args)

    def http(url):
        if "health/ready" in url:
            return 200, "application/json"
        if "classic-new-video-admission" in url:
            return 404, "application/json"
        if "__classic_api_route/status" in url:
            return 200, "text/html"
        raise AssertionError(url)

    monkeypatch.setattr(gate, "_git", git)
    monkeypatch.setattr(gate, "_http_status", http)
    monkeypatch.setattr(gate, "check", lambda *_args, **_kwargs: tasks)
    monkeypatch.setattr(gate, "inspect_backups", lambda *_args: {"verified": True})
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    return main, release, main_dirty, tasks


def test_ready_means_maintenance_preparation_not_approval_to_restart(tmp_path, monkeypatch):
    main, release, _dirty, _tasks = fake_setup(tmp_path, monkeypatch)
    result = gate.release_readiness(main, release)
    assert result["readyForControlledMaintenance"]
    assert result["blockers"] == []
    assert result["ingressFreezeInstalled"] is False
    assert not result["productionChanged"]
    assert "Freeze ALL external new submissions" in result["manualStepsRequired"][1]


def test_dirty_live_main_or_accepted_cut_status_blocks_first_install(tmp_path, monkeypatch):
    main, release, dirty, tasks = fake_setup(tmp_path, monkeypatch)
    dirty["dirty"] = True
    tasks.update(
        {
            "quiescentForVersionSwitch": False,
            "blockers": ["CUT_JOBS_IN_FLIGHT"],
            "counts": {"activeCutMulti": 1},
        }
    )
    result = gate.release_readiness(main, release)
    assert not result["readyForControlledMaintenance"]
    assert set(result["blockers"]) == {"PRODUCTION_MAIN_DIRTY", "CUT_JOBS_IN_FLIGHT"}


def test_rejects_first_install_when_route_or_gate_is_already_installed(
    tmp_path,
    monkeypatch,
):
    main, release, _dirty, _tasks = fake_setup(tmp_path, monkeypatch)
    original = gate._http_status
    monkeypatch.setattr(
        gate,
        "_http_status",
        lambda url: (
            (200, "application/json")
            if "classic-new-video-admission" in url or "__classic_api_route" in url
            else original(url)
        ),
    )
    result = gate.release_readiness(main, release)
    assert not result["readyForControlledMaintenance"]
    assert "FIRST_INSTALL_GATE_STATE_CHANGED" in result["blockers"]
    assert "FIRST_INSTALL_ROUTE_STATE_CHANGED" in result["blockers"]
