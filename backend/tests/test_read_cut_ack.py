"""The mailbox reader must tell "not picked up" apart from "done" and "blocked".

A reader that always reports pending is useless, and one that reports pending as
done would hide a blocked deployment.
"""
from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime, timedelta

import pytest

SCRIPT = "scripts/read_cut_ack.py"


def _mailbox(tmp_path, instructions=(), receipts=(), heartbeat=None):
    coord = tmp_path / "COORD"
    (coord / "instr").mkdir(parents=True)
    (coord / "ack").mkdir(parents=True)
    for name, payload in instructions:
        (coord / "instr" / name).write_text(json.dumps(payload), encoding="utf-8")
    for name, payload in receipts:
        (coord / "ack" / name).write_text(json.dumps(payload), encoding="utf-8")
    if heartbeat is not None:
        (coord / "HEARTBEAT").write_text(heartbeat, encoding="utf-8")
    return tmp_path


def _run(tmp_path, monkeypatch, capsys):
    module = runpy.run_path(SCRIPT)
    monkeypatch.setattr("sys.argv", ["read_cut_ack.py", "--root", str(tmp_path)])
    code = module["main"]()
    return code, capsys.readouterr().out


def test_empty_mailbox_is_not_an_error(tmp_path, monkeypatch, capsys):
    code, out = _run(_mailbox(tmp_path), monkeypatch, capsys)
    assert code == 0, out
    assert "信箱为空" in out


def test_instruction_without_receipt_is_pending(tmp_path, monkeypatch, capsys):
    mailbox = _mailbox(tmp_path, instructions=[("0001-a.json", {
        "instructionId": "0001", "title": "握手",
    })])
    code, out = _run(mailbox, monkeypatch, capsys)
    assert code == 1, out
    assert "PENDING" in out
    assert "0001" in out


def test_done_receipt_settles_the_instruction(tmp_path, monkeypatch, capsys):
    mailbox = _mailbox(
        tmp_path,
        instructions=[("0001-a.json", {"instructionId": "0001", "title": "握手"})],
        receipts=[("0001-a.json", {"instructionId": "0001", "state": "done", "head": "abc123"})],
    )
    code, out = _run(mailbox, monkeypatch, capsys)
    assert code == 0, out
    assert "DONE" in out


def test_blocked_receipt_keeps_the_mailbox_open_and_shows_the_reason(tmp_path, monkeypatch,
                                                                   capsys):
    mailbox = _mailbox(
        tmp_path,
        instructions=[("0001-a.json", {"instructionId": "0001", "title": "部署"})],
        receipts=[("0001-a.json", {
            "instructionId": "0001", "state": "blocked", "head": "abc123",
            "blockedBy": [{"item": "维护窗口", "reason": "生产冻结未授权"}],
        })],
    )
    code, out = _run(mailbox, monkeypatch, capsys)
    assert code == 1, out
    assert "BLOCKED" in out
    assert "生产冻结未授权" in out


def test_partial_pass_results_are_surfaced(tmp_path, monkeypatch, capsys):
    mailbox = _mailbox(
        tmp_path,
        instructions=[("0001-a.json", {"instructionId": "0001", "title": "自检"})],
        receipts=[("0001-a.json", {
            "instructionId": "0001", "state": "done", "head": "abc123",
            "results": [{"item": "旧链路回归", "status": "fail", "evidence": "1 case red"}],
        })],
    )
    code, out = _run(mailbox, monkeypatch, capsys)
    # Claiming "done" while a result failed must not settle the instruction.
    assert code == 1, out
    assert "未通过：旧链路回归（fail）" in out


@pytest.mark.parametrize(("offset", "expected"), [
    (timedelta(seconds=-10), "s 前）"),
    (timedelta(minutes=-30), "s 前）"),
])
def test_heartbeat_age_is_reported(tmp_path, monkeypatch, capsys, offset, expected):
    stamp = (datetime.now(UTC) + offset).isoformat(timespec="seconds")
    code, out = _run(_mailbox(tmp_path, heartbeat=f"{stamp} abc123"), monkeypatch, capsys)
    assert code == 0, out
    assert expected in out
    assert "缺失" not in out


def test_done_with_residual_blockers_still_counts_as_settled(tmp_path, monkeypatch, capsys):
    """A done instruction may disclose blockers owned by another instruction."""
    mailbox = _mailbox(
        tmp_path,
        instructions=[("0002-a.json", {"instructionId": "0002", "title": "部署"})],
        receipts=[("0002-a.json", {
            "instructionId": "0002", "state": "done", "head": "abc123",
            "results": [{"item": "活动版本切换", "status": "pass"}],
            "blockedBy": [{"item": "可信费用 verifier", "reason": "未确认，继续 fail-closed"}],
        })],
    )
    code, out = _run(mailbox, monkeypatch, capsys)
    assert code == 0, out
    assert "DONE" in out
    assert "可信费用 verifier" in out


def test_missing_heartbeat_is_called_out(tmp_path, monkeypatch, capsys):
    code, out = _run(_mailbox(tmp_path), monkeypatch, capsys)
    assert "缺失" in out


def test_missing_coord_directory_fails_loudly(tmp_path, monkeypatch, capsys):
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 2, out
    assert "信箱目录不存在" in out


def test_malformed_instruction_json_does_not_crash(tmp_path, monkeypatch, capsys):
    coord = tmp_path / "COORD"
    (coord / "instr").mkdir(parents=True)
    (coord / "ack").mkdir(parents=True)
    (coord / "instr" / "0001-bad.json").write_text("{not json", encoding="utf-8")
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 1, out
    assert "0001-bad" in out



def test_completed_is_terminal_with_real_release_passed_result(tmp_path, monkeypatch, capsys):
    box = _mailbox(
        tmp_path,
        instructions=[("0040-check.json", {"instructionId": "0040", "title": "real release"})],
        receipts=[("0040-check.json", {
            "instructionId": "0040", "state": "completed",
            "results": [{"state": "CUT_RELEASE_READPACKAGE_PASSED", "sources": 2}],
        })],
    )
    code, out = _run(box, monkeypatch, capsys)
    assert code == 0, out
    assert "COMPLETED" in out


def test_prefix_matched_summary_cannot_settle_instruction(tmp_path, monkeypatch, capsys):
    box = _mailbox(
        tmp_path,
        instructions=[("0041-check.json", {"instructionId": "0041", "title": "probe"})],
        receipts=[("0041-dual-host-final-summary.json", {
            "instructionId": "0041", "state": "completed",
        })],
    )
    code, out = _run(box, monkeypatch, capsys)
    assert code == 1, out
    assert "PENDING" in out


def test_receipt_with_wrong_instruction_id_is_invalid(tmp_path, monkeypatch, capsys):
    box = _mailbox(
        tmp_path,
        instructions=[("0041-check.json", {"instructionId": "0041"})],
        receipts=[("0041-check.json", {"instructionId": "0034", "state": "completed"})],
    )
    code, out = _run(box, monkeypatch, capsys)
    assert code == 1, out
    assert "RECEIPT_ID_MISMATCH" in out


def test_duplicate_instruction_ids_are_invalid_even_if_one_has_ack(tmp_path, monkeypatch, capsys):
    box = _mailbox(
        tmp_path,
        instructions=[
            ("0041-a.json", {"instructionId": "0041"}),
            ("0041-b.json", {"instructionId": "0041"}),
        ],
        receipts=[("0041-a.json", {"instructionId": "0041", "state": "done"})],
    )
    code, out = _run(box, monkeypatch, capsys)
    assert code == 1, out
    assert out.count("DUPLICATE_INSTRUCTION_ID") == 2


def test_result_without_status_does_not_settle_completed(tmp_path, monkeypatch, capsys):
    box = _mailbox(
        tmp_path,
        instructions=[("0042-check.json", {"instructionId": "0042"})],
        receipts=[("0042-check.json", {
            "instructionId": "0042", "state": "completed", "results": [{"item": "unverified"}],
        })],
    )
    code, out = _run(box, monkeypatch, capsys)
    assert code == 1, out
    assert "未通过" in out
