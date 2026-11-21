"""Consumer remains effect-free, exact, idempotent, and fail-closed."""
from __future__ import annotations

import json

from libtv_agent.services.cut_coord_mailbox import consume_once


def create_mailbox(tmp_path, name="0041-exact.json", identity="0041"):
    coord = tmp_path / "COORD"
    (coord / "instr").mkdir(parents=True)
    (coord / "ack").mkdir()
    (coord / "instr" / name).write_text(
        json.dumps({"instructionId": identity}), encoding="utf-8"
    )
    (coord / "ack" / name).write_text(
        json.dumps({"instructionId": identity, "state": "blocked"}), encoding="utf-8"
    )
    return coord


def test_acks_only_read_and_is_idempotent(tmp_path):
    coord = create_mailbox(tmp_path)
    first = consume_once(tmp_path)
    assert first == {"scanned": 1, "new": 1, "unchanged": 0, "pending": 0, "invalid": 0}
    seen = coord / "seen" / "classic" / "0041-exact.json"
    assert seen.exists()
    record = json.loads(seen.read_text(encoding="utf-8"))
    assert record["receiptState"] == "blocked"
    assert record["settled"] is False
    assert record["effect"] == "READ_ACK_ONLY_NO_EXECUTION"
    original = seen.read_bytes()
    assert consume_once(tmp_path)["unchanged"] == 1
    assert seen.read_bytes() == original
    assert not (coord / "outbox").exists()
    assert list((coord / "ack").iterdir()) == [coord / "ack" / "0041-exact.json"]


def test_updated_ack_is_observed_once_without_replay(tmp_path):
    coord = create_mailbox(tmp_path)
    consume_once(tmp_path)
    seen = coord / "seen" / "classic" / "0041-exact.json"
    previous = json.loads(seen.read_text(encoding="utf-8"))
    (coord / "ack" / "0041-exact.json").write_text(
        json.dumps({"instructionId": "0041", "state": "completed"}), encoding="utf-8"
    )
    assert consume_once(tmp_path)["new"] == 1
    latest = json.loads(seen.read_text(encoding="utf-8"))
    assert latest["settled"] is True
    assert latest["receiptSha256"] != previous["receiptSha256"]
    assert consume_once(tmp_path)["unchanged"] == 1


def test_prefix_summary_and_wrong_id_are_never_consumed(tmp_path):
    coord = create_mailbox(tmp_path)
    (coord / "ack" / "0041-exact.json").unlink()
    (coord / "ack" / "0041-summary.json").write_text(
        '{"instructionId":"0041","state":"done"}', encoding="utf-8"
    )
    assert consume_once(tmp_path)["pending"] == 1
    assert not (coord / "seen").exists()
    (coord / "ack" / "0041-exact.json").write_text(
        '{"instructionId":"0040","state":"done"}', encoding="utf-8"
    )
    assert consume_once(tmp_path)["invalid"] == 1
    assert not (coord / "seen").exists()


def test_duplicate_instruction_ids_do_not_create_seen_records(tmp_path):
    coord = create_mailbox(tmp_path)
    (coord / "instr" / "0041-other.json").write_text(
        '{"instructionId":"0041"}', encoding="utf-8"
    )
    assert consume_once(tmp_path)["invalid"] == 2
    assert not (coord / "seen").exists()



def test_reverse_query_only_confirms_observed_receipt(tmp_path):
    import uuid

    from libtv_agent.services.cut_coord_mailbox import answer_reverse_receipt_probes

    coord = create_mailbox(tmp_path)
    consume_once(tmp_path)
    observed = json.loads(
        (coord / "seen/classic/0041-exact.json").read_text(encoding="utf-8")
    )
    request_id = str(uuid.uuid4())
    inbox = coord / "reverse/instr"
    inbox.mkdir(parents=True)
    req = inbox / (request_id + ".json")
    req.write_text(json.dumps({
        "schemaVersion": 1, "requestId": request_id,
        "action": "confirm_receipt_read",
        "receiptFilename": "0041-exact.json",
        "receiptSha256": observed["receiptSha256"],
    }), encoding="utf-8")
    assert answer_reverse_receipt_probes(tmp_path)["replied"] == 1
    reply_path = coord / "reverse/ack" / req.name
    reply = json.loads(reply_path.read_text(encoding="utf-8"))
    assert reply["state"] == "completed"
    assert reply["receiptReadConfirmed"] is True
    assert reply["receiptState"] == "blocked"
    assert reply["effect"] == "READ_ONLY_RECEIPT_CONFIRMATION"
    assert reply["responder"]["role"] == "classic"
    assert reply["responder"]["service"] == "classic-cutflow-worker"
    assert reply["responder"]["hostname"]
    assert reply["responder"]["pid"] > 0
    assert reply["responder"]["identityProof"] == (
        "self_reported_os_metadata_not_cryptographic"
    )
    first = reply_path.read_bytes()
    assert answer_reverse_receipt_probes(tmp_path)["unchanged"] == 1
    assert reply_path.read_bytes() == first


def test_reverse_query_rejects_arbitrary_exec_and_bad_names(tmp_path):
    import uuid

    from libtv_agent.services.cut_coord_mailbox import answer_reverse_receipt_probes

    coord = create_mailbox(tmp_path)
    folder = coord / "reverse/instr"
    folder.mkdir(parents=True)
    ident = str(uuid.uuid4())
    (folder / (ident + ".json")).write_text(json.dumps({
        "schemaVersion": 1, "requestId": ident, "action": "exec",
        "receiptFilename": "0041-exact.json", "receiptSha256": "a"*64,
        "command": "echo hello",
    }), encoding="utf-8")
    assert answer_reverse_receipt_probes(tmp_path)["invalid"] == 1
    assert not (coord / "reverse/ack").exists()


def test_reverse_query_does_not_confirm_wrong_digest(tmp_path):
    import uuid

    from libtv_agent.services.cut_coord_mailbox import answer_reverse_receipt_probes

    coord = create_mailbox(tmp_path)
    consume_once(tmp_path)
    folder = coord / "reverse/instr"
    folder.mkdir(parents=True)
    ident = str(uuid.uuid4())
    (folder / (ident + ".json")).write_text(json.dumps({
        "schemaVersion": 1, "requestId": ident,
        "action": "confirm_receipt_read",
        "receiptFilename": "0041-exact.json",
        "receiptSha256": "a"*64,
    }), encoding="utf-8")
    assert answer_reverse_receipt_probes(tmp_path)["replied"] == 1
    reply = json.loads((coord / "reverse/ack" / (ident + ".json")).read_text())
    assert reply["state"] == "blocked"
    assert reply["receiptReadConfirmed"] is False



def test_reverse_identity_is_captured_from_executing_host_not_request(tmp_path, monkeypatch):
    import uuid

    from libtv_agent.services import cut_coord_mailbox as mailbox

    coord = create_mailbox(tmp_path)
    mailbox.consume_once(tmp_path)
    seen = json.loads((coord / "seen/classic/0041-exact.json").read_text(encoding="utf-8"))
    request_id = str(uuid.uuid4())
    folder = coord / "reverse/instr"
    folder.mkdir(parents=True)
    (folder / (request_id + ".json")).write_text(json.dumps({
        "schemaVersion": 1, "requestId": request_id,
        "action": "confirm_receipt_read",
        "receiptFilename": "0041-exact.json",
        "receiptSha256": seen["receiptSha256"],
        "responder": {"hostname": "attacker", "pid": 1},
    }), encoding="utf-8")
    monkeypatch.setattr(mailbox.socket, "gethostname", lambda: "classic-test-host")
    monkeypatch.setattr(mailbox.os, "getpid", lambda: 4321)
    assert mailbox.answer_reverse_receipt_probes(tmp_path)["replied"] == 1
    reply = json.loads(
        (coord / "reverse/ack" / (request_id + ".json")).read_text(encoding="utf-8")
    )
    assert reply["responder"]["hostname"] == "classic-test-host"
    assert reply["responder"]["pid"] == 4321
    assert reply["responder"]["hostname"] != "attacker"
