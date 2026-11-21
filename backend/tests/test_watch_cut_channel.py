"""The channel watcher must stay quiet unless something actually changed.

Two failure modes matter: crying wolf on every tick (CUT is idle by design once
the mailbox is empty), and going silent on a real transition (route deployed,
gate opened, an instruction settling).
"""
from __future__ import annotations

import runpy

SCRIPT = "scripts/watch_cut_channel.py"


def _module():
    return runpy.run_path(SCRIPT)


def _state(route="MISSING", gate="N/A", instructions=None, waiting=False, stale=True):
    return {
        "probe": {"route": route, "gate": gate},
        "mailbox": {
            "instructions": instructions or {},
            "waiting": waiting,
            "heartbeatStale": stale,
        },
    }


def test_identical_snapshots_report_nothing():
    diff = _module()["diff"]
    state = _state(instructions={"0001": {"state": "blocked"}})
    assert diff(state, state) == []


def test_route_becoming_live_is_reported():
    diff = _module()["diff"]
    changes = diff(_state(route="MISSING"), _state(route="LIVE", gate="CLOSED"))
    assert any("多源路由" in line and "MISSING → LIVE" in line for line in changes)
    assert any("收单门禁" in line and "CLOSED" in line for line in changes)


def test_instruction_settling_is_reported():
    diff = _module()["diff"]
    changes = diff(
        _state(instructions={"0001": {"state": "pending"}}),
        _state(instructions={"0001": {"state": "blocked"}}),
    )
    assert changes == ["指令 0001：pending → blocked"]


def test_new_instruction_appearing_is_reported():
    diff = _module()["diff"]
    changes = diff(
        _state(instructions={"0001": {"state": "blocked"}}),
        _state(instructions={"0001": {"state": "blocked"}, "0002": {"state": "pending"}}),
    )
    assert changes == ["指令 0002：无 → pending"]


def test_stale_heartbeat_is_silent_while_nothing_is_waiting():
    """CUT stops polling when the mailbox is empty; that is not an incident."""
    diff = _module()["diff"]
    changes = diff(
        _state(stale=False, waiting=False),
        _state(stale=True, waiting=False),
    )
    assert changes == []


def test_stale_heartbeat_is_reported_while_an_instruction_is_waiting():
    diff = _module()["diff"]
    changes = diff(
        _state(stale=False, waiting=True),
        _state(stale=True, waiting=True),
    )
    assert any("心跳" in line and "过期" in line for line in changes)


def test_finishing_all_work_announces_idle_once():
    diff = _module()["diff"]
    changes = diff(
        _state(instructions={"0001": {"state": "ack"}}, waiting=True),
        _state(instructions={"0001": {"state": "done"}}, waiting=False),
    )
    assert any("空闲" in line for line in changes)



def test_mailbox_uses_exact_ack_not_prefix_summary(tmp_path, monkeypatch):
    p = tmp_path / "COORD"
    (p / "instr").mkdir(parents=True)
    (p / "ack").mkdir()
    (p / "instr" / "0041-probe.json").write_text(
        '{"instructionId":"0041"}', encoding="utf-8",
    )
    (p / "ack" / "0041-summary.json").write_text(
        '{"instructionId":"0041","state":"done"}', encoding="utf-8",
    )
    mailbox_state = _module()["mailbox_state"]
    monkeypatch.setattr("runpy.run_path", lambda path: {
        "load_channel": lambda _: ("http://cut", "token"),
        "cutflow_environment": lambda _: {"CUTFLOW_HANDOFF_ROOT": str(tmp_path)},
    })
    # mailbox module captures the actual runtime root; a read-only fixture works.
    state = mailbox_state(tmp_path)
    assert state["instructions"]["0041"]["state"] == "pending"
