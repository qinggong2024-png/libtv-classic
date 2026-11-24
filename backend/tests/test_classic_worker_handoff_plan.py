"""Worker release planning is advisory and never starts or stops a process."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_worker_handoff as module  # noqa: E402


def test_busy_workers_are_not_restarted_by_plan(monkeypatch, tmp_path):
    state = tmp_path / "stack.json"
    state.write_text('{"processes":[{"name":"runner"},{"name":"cutflow"}]}', encoding="utf-8")
    monkeypatch.setattr(module, "STATE", state)
    monkeypatch.setattr(module, "STORAGE", tmp_path)
    monkeypatch.setattr(module, "check", lambda path, baseline_uncertain: {
        "blockers": ["CLOUD_ATTEMPTS_IN_FLIGHT", "PAID_UNKNOWN_INCREASED"],
        "counts": {"activeCloudAttempts": 4, "uncertainPaidHistorical": 16},
    })
    import classic_stack
    monkeypatch.setattr(classic_stack, "owned_process", lambda record: True)
    monkeypatch.setattr(module, "restart_held", lambda kind: False)
    result = module.plan_handoff(uncertain_baseline=15)
    assert not result["readyForShortIntakeFreezeAndExclusiveHandoff"]
    assert result["workerProcessesAlive"] == {"runner": True, "cutflow": True}
    assert result["nextAction"] == "keep-existing-workers-and-reconcile-original-tasks"


def test_no_task_blockers_requires_both_worker_identities(monkeypatch, tmp_path):
    state = tmp_path / "stack.json"
    state.write_text('{"processes":[{"name":"runner"}]}', encoding="utf-8")
    monkeypatch.setattr(module, "STATE", state)
    monkeypatch.setattr(module, "STORAGE", tmp_path)
    monkeypatch.setattr(module, "check", lambda path, baseline_uncertain: {
        "blockers": [], "counts": {"activeCloudAttempts": 0},
    })
    import classic_stack
    monkeypatch.setattr(classic_stack, "owned_process", lambda record: True)
    monkeypatch.setattr(module, "restart_held", lambda kind: False)
    assert "WORKER_IDENTITY_UNVERIFIED" in module.plan_handoff(uncertain_baseline=15)["blockers"]
