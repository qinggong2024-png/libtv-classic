"""Fail-closed classic worker handoff: no live processes/provider calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_green_api as green  # noqa: E402
import classic_worker_handoff as gate  # noqa: E402


@pytest.fixture
def synthetic_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "classic-lan"
    (runtime / "runtime").mkdir(parents=True)
    state = runtime / "runtime" / "stack.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gate, "STORAGE", runtime)
    monkeypatch.setattr(gate, "STATE", state)
    monkeypatch.setattr(green, "web_route", lambda: {"activeApiPort": 8002})
    monkeypatch.setattr(
        green,
        "json_get",
        lambda *_: {
            "configured": True,
            "newVideoAdmissionsPaused": True,
            "deploymentId": "libtv-classic-lan-0e3d1c8",
        },
    )
    monkeypatch.setattr(
        gate,
        "check",
        lambda _db, **kwargs: {
            "quiescentForVersionSwitch": True,
            "blockers": [],
            "counts": {},
        },
    )
    return runtime


def test_arm_and_disarm_handoff_are_idempotent_and_do_not_stop_workers(synthetic_runtime):
    gate.arm_handoff(uncertain_baseline=15)
    assert all(gate.restart_held(kind) for kind in gate.KINDS)
    gate.arm_handoff(uncertain_baseline=15)
    gate.disarm_handoff()
    assert not any(gate.restart_held(kind) for kind in gate.KINDS)


def test_active_cut_or_paid_task_refuses_handoff_even_if_pause_is_active(
    synthetic_runtime, monkeypatch
):
    monkeypatch.setattr(
        gate,
        "check",
        lambda *_args, **_kwargs: {
            "quiescentForVersionSwitch": False,
            "blockers": ["CUT_JOBS_IN_FLIGHT"],
        },
    )
    with pytest.raises(RuntimeError, match="leases still active"):
        gate.arm_handoff(uncertain_baseline=15)
    assert not any(gate.restart_held(kind) for kind in gate.KINDS)


def test_second_preflight_rejection_cleans_new_holds(synthetic_runtime, monkeypatch):
    called = 0

    def successive(_db, **_kwargs):
        nonlocal called
        called += 1
        return {"quiescentForVersionSwitch": called == 1}

    monkeypatch.setattr(gate, "check", successive)
    with pytest.raises(RuntimeError, match="leases still active"):
        gate.arm_handoff(uncertain_baseline=15)
    assert called == 2
    assert not any(gate.restart_held(kind) for kind in gate.KINDS)


def test_unknown_handoff_marker_cannot_be_removed(synthetic_runtime):
    marker = gate.hold_file("runner")
    marker.write_text("unknown-manual-note", encoding="utf-8")
    assert gate.restart_held("runner")
    with pytest.raises(RuntimeError, match="unrecognized"):
        gate.disarm_handoff()
    assert marker.read_text(encoding="utf-8") == "unknown-manual-note"


def test_handoff_rejects_if_running_api_gate_is_not_paused(synthetic_runtime, monkeypatch):
    monkeypatch.setattr(
        green,
        "json_get",
        lambda *_: {
            "configured": True,
            "newVideoAdmissionsPaused": False,
            "deploymentId": "libtv-classic-lan-0e3d1c8",
        },
    )
    with pytest.raises(RuntimeError, match="Pause classic intake"):
        gate.arm_handoff(uncertain_baseline=15)
    assert not any(gate.restart_held(kind) for kind in gate.KINDS)
