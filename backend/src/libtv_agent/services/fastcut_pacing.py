"""Narrowly scoped creative pacing guidance for Classic's approved 10s UGC sample.

This is a PLANNING preset. Physical edits belong to CUT, since Kling ignored
two of the requested four cuts in the five-shot G generation trial.
"""
from __future__ import annotations

PROFILE = "allwe-ugc-five-shot-pacing-v1"
REFERENCE_DURATION_SECONDS = 10
REFERENCE_SHOT_DURATIONS = (1.83, 1.80, 2.30, 2.85, 1.22)


def eligible(*, enabled: bool, duration_seconds: int, cut_mode: str) -> bool:
    return bool(enabled and duration_seconds == 10 and cut_mode == "explicit_cuts")


def director_guidance() -> str:
    return (
        "APPROVED ALLWE 10s FASTCUT CREATIVE PACING REFERENCE: "
        "Plan five DISTINCT useful visual moments for a later edited UGC ad, "
        "not five small poses spread across one continuous shot. "
        "Use the observed reference rhythm as a soft editorial guide: "
        "about 1.8s attention hook, 1.8s legible front-artwork close-up, "
        "2.3s natural wearer/outfit reaction, 2.8s DIFFERENT product evidence "
        "only if visibly grounded by the inputs, then about 1.2s front-facing payoff. "
        "Each new view must introduce useful buyer information. "
        "Keep the final shots intelligible: do not stack sub-second cuts or rush "
        "through the final product return. Prioritize an immediate hook, "
        "readable artwork, credible everyday motion and a clear closing product view. "
        "Generate natural reusable source moments; downstream CUT will be responsible "
        "for actual hard cuts. This is creative guidance, not a promise that Kling "
        "will physically implement five edits. Do not invent scenes, text, "
        "new people, garment features, promotions or purchase urgency. "
        "Respect first-frame pose, original garment and normal human speed."
    )
