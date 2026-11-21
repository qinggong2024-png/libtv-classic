"""Snapshot the classic <-> CUT channel and report only what changed.

Polling on the classic side has to be change-driven, otherwise every tick is
noise. This script captures two things and diffs them against the previous run:

* the probe result (is the multi-source route live, is CUT's gate open)
* the mailbox (per-instruction state, heartbeat age)

Exit codes
    0  first run (baseline written) or nothing changed
    1  something changed — the printed lines say what
    2  cannot read local configuration

Nothing here talks to CUT beyond the same read-only GETs the probe already does,
and nothing is ever created on the remote side.

Usage
    python scripts/watch_cut_channel.py
    python scripts/watch_cut_channel.py --state .workbuddy/cut-channel-state.json
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / ".workbuddy" / "cut-channel-state.json"
MAILBOX = runpy.run_path(str(ROOT / "backend/src/libtv_agent/services/cut_coord_mailbox.py"))
duplicate_ids = MAILBOX["duplicate_ids"]
exact_receipt = MAILBOX["exact_receipt"]
read_json = MAILBOX["read_json"]
HEARTBEAT_STALE_SECONDS = 180


def _probe_module() -> dict:
    return runpy.run_path(str(ROOT / "scripts" / "check_cut_multi_route.py"))


def probe_state(storage: Path) -> dict:
    module = _probe_module()
    channel = module["load_channel"](storage)
    if channel is None:
        return {"error": "本机 CUT 通道配置不可读"}
    base, token = channel
    probe = str(uuid.uuid4())
    status, content_type, body = module["get"](base, module["MULTI"] + probe, token)
    kind = module["classify"](status, content_type, body)
    if kind == module["CATCH_ALL"]:
        route = "MISSING"
    elif kind == module["APP"]:
        route = "LIVE"
    elif kind == module["UPSTREAM"]:
        route = "UPSTREAM"
    else:
        route = "UNREACHABLE"
    closed = route == "LIVE" and module["gate_closed"](status, body)
    return {
        "route": route,
        "httpStatus": status,
        "gate": "CLOSED" if closed else ("OPEN" if route == "LIVE" else "N/A"),
    }


def mailbox_state(storage: Path) -> dict:
    module = runpy.run_path(str(ROOT / "scripts" / "check_cut_multi_route.py"))
    channel = module["load_channel"](storage)
    if channel is None:
        return {"error": "本机 CUT 通道配置不可读"}
    root = Path(runpy.run_path(str(ROOT / "scripts" / "classic_cutflow.py"))
                ["cutflow_environment"](storage)["CUTFLOW_HANDOFF_ROOT"])
    coord = root / "COORD"
    instructions: dict[str, dict] = {}
    instr_dir = coord / "instr"
    if instr_dir.is_dir():
        paths = sorted(instr_dir.glob("*.json"))
        duplicates = duplicate_ids(paths)
        for path in paths:
            payload = read_json(path) or {}
            instruction_id = str(payload.get("instructionId") or path.stem)
            receipt, issue = exact_receipt(coord, path, duplicates)
            key = path.name if instruction_id in duplicates else instruction_id
            instructions[key] = {
                "state": (
                    "invalid" if issue
                    else str((receipt or {}).get("state") or "pending").lower()
                ),
                "finishedAt": (receipt or {}).get("finishedAt"),
                **({"error": issue} if issue else {}),
            }
    heartbeat = None
    beat_path = coord / "HEARTBEAT"
    if beat_path.is_file():
        try:
            raw = beat_path.read_text(encoding="utf-8").strip()
            stamp = raw.split(" ")[0] if raw else ""
            when = datetime.fromisoformat(stamp)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            heartbeat = int((datetime.now(UTC) - when).total_seconds())
        except (OSError, ValueError):
            heartbeat = None
    return {
        "instructions": instructions,
        "heartbeatSeconds": heartbeat,
        # A stale heartbeat only matters while something is waiting on CUT. Once
        # CUT has stopped polling because the mailbox is empty, it is idle by
        # design and must not raise an alarm on every tick.
        "waiting": any(v["state"] in {"pending", "ack"} for v in instructions.values()),
        "heartbeatStale": heartbeat is None or heartbeat > HEARTBEAT_STALE_SECONDS,
    }


def load_previous(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def diff(previous: dict, current: dict) -> list[str]:
    changes = []
    old_probe = previous.get("probe") or {}
    new_probe = current.get("probe") or {}
    for key, label in (("route", "多源路由"), ("gate", "CUT 收单门禁")):
        if old_probe.get(key) != new_probe.get(key):
            changes.append(f"{label}：{old_probe.get(key)} → {new_probe.get(key)}")
    old_box = (previous.get("mailbox") or {}).get("instructions") or {}
    new_box = (current.get("mailbox") or {}).get("instructions") or {}
    for instruction_id in sorted(set(old_box) | set(new_box)):
        before = (old_box.get(instruction_id) or {}).get("state", "无")
        after = (new_box.get(instruction_id) or {}).get("state", "无")
        if before != after:
            changes.append(f"指令 {instruction_id}：{before} → {after}")
    old_stale = (previous.get("mailbox") or {}).get("heartbeatStale")
    new_stale = (current.get("mailbox") or {}).get("heartbeatStale")
    waiting = (current.get("mailbox") or {}).get("waiting")
    if waiting:
        if old_stale != new_stale:
            changes.append("心跳：" + ("已过期（CUT 可能停了轮询）" if new_stale else "恢复正常"))
    else:
        old_waiting = (previous.get("mailbox") or {}).get("waiting")
        if old_waiting and not waiting:
            changes.append("CUT 已处理完所有指令，进入空闲（停轮询属正常）")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", default="var/classic-lan")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()

    storage = ((ROOT / args.storage) if not Path(args.storage).is_absolute()
               else Path(args.storage))
    probe = probe_state(storage)
    mailbox = mailbox_state(storage)
    if "error" in probe or "error" in mailbox:
        print("FAIL " + str(probe.get("error") or mailbox.get("error")))
        return 2
    current = {
        "capturedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "probe": probe,
        "mailbox": mailbox,
    }

    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    previous = load_previous(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    route = probe["route"]
    gate = probe["gate"]
    box = mailbox["instructions"]
    beat = "过期" if mailbox["heartbeatStale"] else f"{mailbox['heartbeatSeconds']}s"
    blocked = any(v["state"] == "blocked" for v in box.values())
    # Waiting on CUT outranks "blocked": a pending instruction means CUT owes us
    # work, so a stale heartbeat is a real signal and must not read as idle.
    if mailbox["waiting"]:
        state_note = "（等待 CUT 取件）"
    elif blocked:
        state_note = "（等待人工授权）"
    else:
        state_note = "（CUT 空闲）"
    listed = ", ".join(f"{k}:{v['state']}" for k, v in box.items()) or "无"
    summary = f"路由={route} 门禁={gate} 指令={listed} 心跳={beat}{state_note}"

    if previous is None:
        print("首次运行，已建立基线")
        print(summary)
        return 0

    changes = diff(previous, current)
    if not changes:
        print("无变化")
        print(summary)
        return 0
    print("有变化")
    for line in changes:
        print("  - " + line)
    print(summary)
    return 1


if __name__ == "__main__":
    sys.exit(main())
