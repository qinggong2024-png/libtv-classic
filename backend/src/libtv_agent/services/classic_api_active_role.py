"""Classic LAN API background recovery ownership across a guarded route flip.

No configured file means normal behavior for all non-classic installations.
The shared route marks which of two same-database APIs may run periodic
recovery. This is a *cooperative* guard; it is not a distributed lease and
must not replace a quiet preflight or proof that old jobs have drained.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from libtv_agent.config import Settings

logger = logging.getLogger(__name__)


def api_owns_background_recovery(settings: Settings) -> bool:
    path: Path | None = settings.classic_api_route_file
    if path is None:
        return True
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The initial classic API is 8002 until an explicit route is applied.
        return settings.app_port == 8002
    except OSError:
        logger.warning("classic API routing file is unreadable; background recovery stopped")
        return False
    try:
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "activeApiPort"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["activeApiPort"]) is not int
            or data["activeApiPort"] not in (8002, 8003)
        ):
            raise ValueError("invalid route")
        return settings.app_port == data["activeApiPort"]
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        logger.warning("classic API routing file is invalid; background recovery stopped")
        return False
