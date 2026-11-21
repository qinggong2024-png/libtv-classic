"""Read-only manager warnings for shared Kling member credits.

The account balance is never used to reject or reserve paid submissions.
"""
from __future__ import annotations

from math import ceil


def cli_credit_warning(available_credits: int, last_seven_day_credits: int) -> dict:
    if available_credits < 0 or last_seven_day_credits < 0:
        raise ValueError("credit totals must not be negative")
    # Two days of observed CLI usage, with a small fallback alert threshold
    # when this ALLWE installation has little history. External uses are unknown.
    threshold = max(500, ceil(last_seven_day_credits * 2 / 7))
    daily = last_seven_day_credits / 7
    return {
        "status": "ready",
        "availableCredits": available_credits,
        "recent7DayCredits": last_seven_day_credits,
        "warningThresholdCredits": threshold,
        "estimatedDaysRemaining": round(available_credits / daily, 1) if daily else None,
        "lowBalance": available_credits < threshold,
        "admissionBlocked": False,
    }
