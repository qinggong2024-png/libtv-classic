"""Read the classic <-> CUT mailbox under ``<handoffRoot>/COORD``.

The mailbox is deliberately file-based: instructions are written by the classic
side into ``instr/``, CUT polls them and writes receipts into ``ack/``. This
reader is read-only and answers three questions in one shot:

* is CUT actually polling (how old is ``HEARTBEAT``)?
* which instructions have no receipt yet?
* what did CUT answer for the ones it processed?

Exit codes
    0  every instruction has a ``done`` receipt
    1  something is pending, acknowledged-but-unfinished, blocked or rejected
    2  mailbox or local CUT configuration is unreadable
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAILBOX = runpy.run_path(str(ROOT / "backend/src/libtv_agent/services/cut_coord_mailbox.py"))
duplicate_ids = MAILBOX["duplicate_ids"]
exact_receipt = MAILBOX["exact_receipt"]
settled = MAILBOX["settled"]
successful_result = MAILBOX["successful_result"]
COORD = "COORD"


def load_root(storage: Path) -> Path | None:
    try:
        environment = runpy.run_path(str(ROOT / "scripts" / "classic_cutflow.py"))
        values = environment["cutflow_environment"](storage)
    except (SystemExit, KeyError):
        return None
    root = values.get("CUTFLOW_HANDOFF_ROOT")
    return Path(root) if root else None


def read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def heartbeat_age(coord: Path) -> str:
    path = coord / "HEARTBEAT"
    if not path.is_file():
        return "缺失（CUT 可能未轮询）"
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "不可读"
    stamp = raw.split(" ")[0] if raw else ""
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return f"格式无法解析：{raw[:40]}"
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = int((datetime.now(UTC) - when).total_seconds())
    return f"{stamp}（{seconds}s 前）"


def describe(receipt: dict | None) -> tuple[str, str]:
    if receipt is None:
        return "PENDING", "尚无回执（CUT 未取件或未写回）"
    state = str(receipt.get("state") or "?").lower()
    detail = [f"head={str(receipt.get('head') or '?')[:8]}"]
    for item in receipt.get("blockedBy") or []:
        if isinstance(item, dict):
            detail.append(f"阻塞：{item.get('item')}（{item.get('reason')}）")
    for item in receipt.get("results") or []:
        if not successful_result(item):
            if isinstance(item, dict):
                result_state = item.get("status", item.get("state"))
                detail.append(f"未通过：{item.get('item')}（{result_state}）")
            else:
                detail.append("未通过：无效的结果项")
    if receipt.get("nextAction"):
        detail.append(f"下一步：{receipt['nextAction']}")
    if receipt.get("notes"):
        detail.append(str(receipt["notes"])[:120])
    return state.upper(), "；".join(detail)


def has_failure(receipt: dict | None) -> bool:
    """Only a failing *result* contradicts a "done" receipt.

    A done instruction may still disclose residual `blockedBy` entries that are
    outside its own scope (for example dependencies another instruction owns).
    Counting those as unsettled would report "unfinished" forever, so `state`
    plus `results` are the objective signal and `blockedBy` is disclosure.
    """
    if receipt is None:
        return False
    results = receipt.get("results", [])
    if not isinstance(results, list):
        return True
    return any(not successful_result(item) for item in results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", default="var/classic-lan", help="classic stack storage dir")
    parser.add_argument("--root", default=None, help="override handoff root")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root)
    else:
        storage = ((ROOT / args.storage) if not Path(args.storage).is_absolute()
                   else Path(args.storage))
        root = load_root(storage)
    if root is None:
        print("FAIL 本机 CUT 通道配置不可读（cutflow-config.json 缺失）")
        return 2
    coord = root / COORD
    if not coord.is_dir():
        print(f"FAIL 信箱目录不存在：{coord}")
        return 2
    print(f"共享根         {root}")
    print(f"心跳           {heartbeat_age(coord)}")

    instructions = sorted((coord / "instr").glob("*.json")) if (coord / "instr").is_dir() else []
    if not instructions:
        print("\n信箱为空：instr/ 下没有指令。")
        return 0

    print(f"\n{'ID':<8}{'状态':<10}标题")
    pending = 0
    duplicates = duplicate_ids(instructions)
    for path in instructions:
        instruction = read_json(path) or {}
        instruction_id = str(instruction.get("instructionId") or path.stem)
        receipt, issue = exact_receipt(coord, path, duplicates)
        state, detail = ("INVALID", issue) if issue else describe(receipt)
        if issue or not settled(receipt):
            pending += 1
        title = str(instruction.get("title") or "（无标题）")
        print(f"{instruction_id:<8}{state:<10}{title}")
        if detail:
            print(f"{'':<18}{detail}")

    print()
    if pending == 0:
        print(f"结论           全部 {len(instructions)} 条指令均已完成")
        return 0
    print(f"结论           有 {pending} 条未完成，继续等 CUT 轮询或处理阻塞")
    return 1


if __name__ == "__main__":
    sys.exit(main())
