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


@pytest.mark.parametrize("kind,unrelated", [
    ("runner", ["CUT_JOBS_IN_FLIGHT"]),
    ("cutflow", ["CLOUD_ATTEMPTS_IN_FLIGHT", "PAID_COMMANDS_IN_FLIGHT"]),
])
def test_scoped_independent_work_informational_only_with_verified_owner(kind, unrelated, monkeypatch):
    report = {"blockers": unrelated, "quiescentForVersionSwitch": False, "counts": {}}
    assert gate.scoped_task_gate(report)["blockers"] == unrelated
    monkeypatch.setattr(gate, "independent_worker_verified", lambda kind: False)
    assert "INDEPENDENT_WORKER_UNVERIFIED" in gate.scoped_task_gate(report, kind)["blockers"]
    monkeypatch.setattr(gate, "independent_worker_verified", lambda kind: True)
    scoped = gate.scoped_task_gate(report, kind)
    assert scoped["quiescentForVersionSwitch"]
    assert scoped["informationalActiveWork"] == unrelated
    assert scoped["blockers"] == []
    report["blockers"] = unrelated + ["PAID_UNKNOWN_INCREASED"]
    assert gate.scoped_task_gate(report, kind)["blockers"] == ["PAID_UNKNOWN_INCREASED"]


def test_runner_still_blocks_own_paid_commands(monkeypatch):
    monkeypatch.setattr(gate, "independent_worker_verified", lambda kind: True)
    result = gate.scoped_task_gate({"blockers": ["CUT_JOBS_IN_FLIGHT", "PAID_COMMANDS_IN_FLIGHT"]}, "runner")
    assert result["blockers"] == ["PAID_COMMANDS_IN_FLIGHT"]


def test_scope_does_not_accept_missing_task_report():
    assert not gate.scoped_task_gate({}, "runner")["quiescentForVersionSwitch"]


def test_scoped_source_rejects_shared_runtime_changes(tmp_path, monkeypatch):
    import classic_worker_checkpoint as checkpoint
    monkeypatch.setattr(checkpoint, "verify_worker_compatibility", lambda *args: None)
    old, new = tmp_path / "old", tmp_path / "new"
    target = "backend/src/libtv_agent/services/ootd_native_direct.py"
    for root, body in ((old, "old"), (new, "new")):
        f = root / target
        f.parent.mkdir(parents=True)
        f.write_text(body)
    checkpoint.verify_scoped_worker_source(old, new, "runner")
    (new / "backend/src/libtv_agent/services/fixed_video_tasks.py").write_text("changed")
    with pytest.raises(RuntimeError, match="shared runtime contracts"):
        checkpoint.verify_scoped_worker_source(old, new, "runner")


def test_explicit_runner_hold_does_not_hold_cut_worker(synthetic_runtime, monkeypatch):
    monkeypatch.setattr(gate, "check", lambda *_args, **_kwargs: {
        "blockers": ["CUT_JOBS_IN_FLIGHT"], "quiescentForVersionSwitch": False, "counts": {},
    })
    monkeypatch.setattr(gate, "independent_worker_verified", lambda kind: True)
    result = gate.arm_handoff(uncertain_baseline=15, kind="runner")
    assert result["informationalActiveWork"] == ["CUT_JOBS_IN_FLIGHT"]
    assert gate.restart_held("runner") and not gate.restart_held("cutflow")
    gate.disarm_handoff("runner")
