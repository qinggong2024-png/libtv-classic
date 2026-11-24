"""Blocked worker plan must return nonzero for shell automation."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import classic_worker_handoff as module  # noqa: E402


def test_blocked_worker_plan_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["classic_worker_handoff.py", "plan"])
    monkeypatch.setattr(module, "plan_handoff", lambda uncertain_baseline: {
        "readyForShortIntakeFreezeAndExclusiveHandoff": False,
        "blockers": ["CUT_JOBS_IN_FLIGHT"],
    })
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert "CUT_JOBS_IN_FLIGHT" in capsys.readouterr().out


def test_ready_worker_plan_exits_normally(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["classic_worker_handoff.py", "plan"])
    monkeypatch.setattr(module, "plan_handoff", lambda uncertain_baseline: {
        "readyForShortIntakeFreezeAndExclusiveHandoff": True,
        "blockers": [],
    })
    assert module.main() is None
    assert '"blockers": []' in capsys.readouterr().out
