"""ALLWE budget valuation for Kling CLI; credits are NOT an admission quota.

The current Kling 3.0 Omni CLI image-to-video call does not override its
1080p / enable_audio=true defaults. Other models retain the existing global
per-second USD quote until their provider pricing is explicitly mapped.
"""
from __future__ import annotations

KLING_CLI_MICROUSD_PER_CREDIT = 10_850  # $0.01085 internal USD allocation
KLING_3_OMNI_DEFAULT_CREDITS_PER_SECOND = 12  # 1080p, native audio on


def estimate_cli_credits(provider: str, model: str | None, duration_seconds: int) -> int | None:
    """Return a supported pre-submit quote, never a provider balance requirement."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if provider == "kling_official_cli" and model == "kling-video-v3_0_omni":
        return duration_seconds * KLING_3_OMNI_DEFAULT_CREDITS_PER_SECOND
    return None


def credits_to_microusd(credits: int) -> int:
    """Convert receipt credits into internal USD micro-units, not cash charges."""
    if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
        raise ValueError("credits must be a nonnegative integer")
    return credits * KLING_CLI_MICROUSD_PER_CREDIT
