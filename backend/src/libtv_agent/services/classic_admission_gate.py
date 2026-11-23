"""Fail-closed, opt-in CLASSIC 5176 new-video admission gate.

This gate is intentionally limited to HTTP *new video creation*. It does not
stop cloud polling, downloads, Sol plans already accepted, nor CUT retries.
No database or provider calls. A flag is read for each new request so the
classic API can pause admissions without restarting the process.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, status
from libtv_agent.config import Settings

logger = logging.getLogger(__name__)

PAUSED_CODE = "CLASSIC_NEW_VIDEO_TASKS_PAUSED"


def new_video_admission_paused(settings: Settings) -> bool:
    pause_file: Path | None = settings.classic_new_video_pause_file
    if pause_file is None:
        return False
    try:
        # stat on each incoming POST, never cache: operator changes take effect
        # in a running API process. Permission errors fail closed.
        pause_file.stat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("classic new-video admission flag cannot be inspected")
        return True


def require_new_video_admission(settings: Settings) -> None:
    if new_video_admission_paused(settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": PAUSED_CODE,
                "message": (
                    "Classic video creation is temporarily paused for a safe update. "
                    "Existing tasks, downloads and editing continue."
                ),
            },
            headers={"Retry-After": "60"},
        )
