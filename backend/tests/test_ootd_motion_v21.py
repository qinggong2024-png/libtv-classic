"""V2.2 adds source-backed front/back apparel views while keeping V2.1 motion protection."""
from __future__ import annotations

from libtv_agent.services.ootd_simple import (
    PROFILE, HISTORY_PROFILES, SYSTEM_PROMPT,
    KLING_ONLY_PRODUCT_SYSTEM_PROMPT, MULTI_REFERENCE_SYSTEM_PROMPT,
    MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
)


def test_active_cross_scene_profile_keeps_prior_motion_history():
    assert PROFILE == "allwe-ootd-sol-dynamic-display-v1"
    assert "allwe-ootd-sol-simple-v1" in HISTORY_PROFILES
    assert "allwe-ootd-sol-motion-v2.1" in HISTORY_PROFILES
    assert PROFILE in HISTORY_PROFILES


def test_v21_garment_first_lively_motion_and_no_fixed_storyboard_for_all_routes():
    for prompt in (
        SYSTEM_PROMPT, KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
        MULTI_REFERENCE_SYSTEM_PROMPT, MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
    ):
        assert "Never grip, stretch, tug sideways or downward" in prompt
        assert "fabric before moving, without grasping, pinching or pulling" in prompt
        assert "first-frame hand positions" in prompt
        assert "feet, hips, shoulders, head and hair may" in prompt
        assert "In each chosen shot, give both hands a clear plausible motion path" in prompt
        assert "a hair or face gesture only if it follows naturally" in prompt
        assert "center and preserve the important printed design" in prompt
        assert "Never stage standalone side-profile" in prompt
        assert "FRONT artwork and fabric closeups" in prompt
        assert "BACK views only when" in prompt
        assert "do not favor the face while cropping the garment artwork" in prompt
        assert "normal human speed and lively purposeful action" in prompt
        assert "Sol independently chooses shot count, durations" in prompt
        assert "Do not enforce a fixed wide-close-wide" in prompt
        assert "User additional instructions have the highest" in prompt
        assert "No JSON" in prompt
        assert "action library" not in prompt.lower()
