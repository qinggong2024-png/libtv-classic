"""Strict, side-effect-free matching for COORD instruction/ack pairs.

Only an exact same-name receipt may settle an instruction. No prefix fallback,
because summary receipts and historical IDs intentionally share prefixes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ID = re.compile(r"^[0-9]{4}$")
SUCCESS_STATES = frozenset({"done", "completed", "completed_confirmed"})
SUCCESS_RESULTS = frozenset({"pass", "passed", "ok", "success", "succeeded", "completed", "done"})


def read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def instruction_identity(path: Path, payload: dict | None) -> str | None:
    # Reject malformed instructions instead of inferring authority from a filename.
    if not isinstance(payload, dict):
        return None
    identity = payload.get("instructionId")
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        return None
    if not path.name.startswith(identity + "-"):
        return None
    return identity


def duplicate_ids(paths: list[Path]) -> set[str]:
    ids = Counter()
    for path in paths:
        payload = read_json(path)
        identity = instruction_identity(path, payload)
        if identity:
            ids[identity] += 1
    return {identity for identity, count in ids.items() if count > 1}


def exact_receipt(
    coord: Path, path: Path, duplicates: set[str] | None = None,
) -> tuple[dict | None, str | None]:
    instruction = read_json(path)
    identity = instruction_identity(path, instruction)
    if identity is None:
        return None, "INVALID_INSTRUCTION"
    if identity in (duplicates or set()):
        return None, "DUPLICATE_INSTRUCTION_ID"
    receipt_path = coord / "ack" / path.name
    if not receipt_path.exists():
        return None, None
    receipt = read_json(receipt_path)
    if receipt is None:
        return None, "INVALID_RECEIPT"
    if receipt.get("instructionId") != identity:
        return None, "RECEIPT_ID_MISMATCH"
    return receipt, None


def successful_result(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    state = item.get("status", item.get("state"))
    if not isinstance(state, str):
        return False
    normalized = state.lower()
    return normalized in SUCCESS_RESULTS or normalized.endswith("_passed")


def settled(receipt: dict | None) -> bool:
    if receipt is None or str(receipt.get("state", "")).lower() not in SUCCESS_STATES:
        return False
    results = receipt.get("results", [])
    if not isinstance(results, list):
        return False
    return all(successful_result(item) for item in results)



def consume_once(handoff_root: Path) -> dict[str, int]:
    """Acknowledge *reading* exact matching receipts, never execute instructions.

    Safe to rerun: unchanged receipts produce no writes. Only COORD/seen/classic
    is modified; CUT's instr, ack and business inbox/outbox remain immutable.
    """
    coord = handoff_root / "COORD"
    if not (coord / "instr").is_dir() or not (coord / "ack").is_dir():
        raise FileNotFoundError("COORD inbox/ack directories unavailable")
    paths = sorted((coord / "instr").glob("*.json"))
    duplicates = duplicate_ids(paths)
    counters = {"scanned": len(paths), "new": 0, "unchanged": 0, "pending": 0, "invalid": 0}
    seen_root = coord / "seen" / "classic"
    for path in paths:
        receipt, issue = exact_receipt(coord, path, duplicates)
        if issue:
            counters["invalid"] += 1
            continue
        if receipt is None:
            counters["pending"] += 1
            continue
        source = coord / "ack" / path.name
        try:
            raw = source.read_bytes()
            if len(raw) > 1_048_576:
                counters["invalid"] += 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            instruction_raw = path.read_bytes()
            if len(instruction_raw) > 1_048_576:
                counters["invalid"] += 1
                continue
            instruction_digest = hashlib.sha256(instruction_raw).hexdigest()
        except OSError:
            counters["invalid"] += 1
            continue
        target = seen_root / path.name
        previous = read_json(target)
        if (previous is not None
            and previous.get("receiptSha256") == digest
            and previous.get("instructionSha256") == instruction_digest):
            counters["unchanged"] += 1
            continue
        seen_root.mkdir(parents=True, exist_ok=True)
        record = {
            "schemaVersion": 1,
            "reader": "classic",
            "instructionId": receipt["instructionId"],
            "receiptFilename": path.name,
            "receiptSha256": digest,
            "instructionSha256": instruction_digest,
            "receiptState": str(receipt.get("state") or "unknown").lower(),
            "settled": settled(receipt),
            "observedAt": datetime.now(UTC).isoformat(),
            "effect": "READ_ACK_ONLY_NO_EXECUTION",
        }
        tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        counters["new"] += 1
    return counters



def answer_reverse_receipt_probes(handoff_root: Path) -> dict[str, int]:
    """Allowlisted reverse COORD query: prove Classic saw an exact CUT receipt.

    This channel has no arbitrary command runner and no access to generation,
    imports or Provider endpoints. The caller-provided identity is not trusted.
    """
    coord = handoff_root / "COORD"
    inbox = coord / "reverse" / "instr"
    counters = {"scanned": 0, "replied": 0, "unchanged": 0, "invalid": 0}
    if not inbox.is_dir():
        return counters
    ack_root = coord / "reverse" / "ack"
    for request_file in sorted(inbox.glob("*.json")):
        counters["scanned"] += 1
        try:
            request_id = str(uuid.UUID(request_file.stem))
        except ValueError:
            counters["invalid"] += 1
            continue
        if request_file.name != request_id + ".json":
            counters["invalid"] += 1
            continue
        try:
            raw = request_file.read_bytes()
        except OSError:
            counters["invalid"] += 1
            continue
        if len(raw) > 8_192:
            counters["invalid"] += 1
            continue
        request = read_json(request_file)
        if not isinstance(request, dict):
            counters["invalid"] += 1
            continue
        filename = request.get("receiptFilename")
        digest = request.get("receiptSha256")
        if (
            request.get("schemaVersion") != 1
            or request.get("requestId") != request_id
            or request.get("action") != "confirm_receipt_read"
            or not isinstance(filename, str)
            or not re.fullmatch(r"[0-9]{4}-[A-Za-z0-9_.-]{1,150}\.json", filename)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            counters["invalid"] += 1
            continue
        request_sha = hashlib.sha256(raw).hexdigest()
        ack_path = ack_root / request_file.name
        previous = read_json(ack_path)
        if ack_path.exists():
            if previous and previous.get("requestSha256") == request_sha:
                counters["unchanged"] += 1
            else:
                # Existing request ID with different content is a conflict.
                counters["invalid"] += 1
            continue
        seen = read_json(coord / "seen" / "classic" / filename)
        is_match = (
            isinstance(seen, dict)
            and seen.get("receiptFilename") == filename
            and seen.get("receiptSha256") == digest
            and seen.get("effect") == "READ_ACK_ONLY_NO_EXECUTION"
        )
        reply = {
            "schemaVersion": 1,
            "requestId": request_id,
            "action": "confirm_receipt_read",
            "requestSha256": request_sha,
            "state": "completed" if is_match else "blocked",
            "receiptReadConfirmed": bool(is_match),
            "receiptState": seen.get("receiptState") if is_match else None,
            "receiptSha256": digest,
            "respondedAt": datetime.now(UTC).isoformat(),
            "effect": "READ_ONLY_RECEIPT_CONFIRMATION",
            "responder": {
                "role": "classic",
                "service": "classic-cutflow-worker",
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "identityProof": "self_reported_os_metadata_not_cryptographic",
            },
        }
        ack_root.mkdir(parents=True, exist_ok=True)
        tmp = ack_path.with_name(ack_path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
            if not ack_path.exists():
                os.replace(tmp, ack_path)
                counters["replied"] += 1
        finally:
            tmp.unlink(missing_ok=True)
    return counters
