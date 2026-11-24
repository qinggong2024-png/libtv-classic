"""Operator-only CLASSIC 5176 HTTP new-video admission freeze.

The flag is owned by this classic LAN runtime. Does not pause old provider
receipts, Runner/CUT workers, downloads, or already accepted tasks.
This script is NOT a production deploy or process-management command.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
NAME = "pause-new-video-tasks.flag"
TOKEN = "allwe-classic-new-video-admissions-v1"
LIVE_STATUS_URL = "http://127.0.0.1:8002/api/v1/system/classic-new-video-admission"
EXPECTED_DEPLOYMENT = "libtv-classic-lan-0e3d1c8"


def verify_live_api_gate() -> None:
    """Never report 'paused' when the old live API does not honor the flag."""
    try:
        with build_opener(ProxyHandler({})).open(LIVE_STATUS_URL, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("classic API admission status unavailable")
            status = json.loads(response.read(8192))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "the running classic API has not confirmed intake-gate support; "
            "refusing to claim new-task intake is paused"
        ) from exc
    if (
        not isinstance(status, dict)
        or status.get("gateVersion") != 1
        or status.get("deploymentId") != EXPECTED_DEPLOYMENT
        or status.get("configured") is not True
        or status.get("scope") != "new-video-create-only"
    ):
        raise RuntimeError("classic API running version does not support the intake gate")


def pause_file(storage: Path) -> Path:
    return storage / "runtime" / NAME


def set_admissions(storage: Path, *, pause: bool) -> dict:
    path = pause_file(storage)
    if not storage.is_dir() or not path.parent.is_dir():
        raise RuntimeError("classic runtime directory missing; refusing to create it")
    if pause:
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(TOKEN + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            if path.read_text(encoding="utf-8").strip() != TOKEN:
                raise RuntimeError("unrecognized pause flag; refusing overwrite") from exc
    else:
        if path.is_file():
            if path.read_text(encoding="utf-8").strip() != TOKEN:
                raise RuntimeError("unrecognized pause flag; refusing removal")
            path.unlink()
        elif path.exists():
            raise RuntimeError("pause flag is not a regular file")
    return {"newVideoAdmissionsPaused": path.exists(), "scope": "new-video-create-only"}


def read_status(storage: Path) -> dict:
    path = pause_file(storage)
    if path.is_file() and path.read_text(encoding="utf-8").strip() != TOKEN:
        raise RuntimeError("unrecognized pause flag; requires operator inspection")
    return {"newVideoAdmissionsPaused": path.exists(), "scope": "new-video-create-only"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "resume", "status"))
    args = parser.parse_args()
    storage = ROOT / "var" / "classic-lan"
    if args.action != "status":
        verify_live_api_gate()
    result = (
        read_status(storage)
        if args.action == "status"
        else set_admissions(storage, pause=args.action == "pause")
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
