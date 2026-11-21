"""Offline guard: acting guidance cannot grow the prompt or the output contract."""

from libtv_agent.services.ai_creative_planner import CREATIVE_SYSTEM_PROMPT


def test_expression_guidance_replaces_old_text_without_growth():
    # Baseline system prompt size, before the B2-inspired wording change.
    # This bounds *text length*, not unknown image or relay reasoning tokens.
    assert len(CREATIVE_SYSTEM_PROMPT) <= 6669
    assert "scene or print cue shift eyes/face" in CREATIVE_SYSTEM_PROMPT
    assert "Carry reactions through cuts" in CREATIVE_SYSTEM_PROMPT
    assert "face and key print together in a medium shot" in CREATIVE_SYSTEM_PROMPT
    assert "Avoid forced laughs or gestures" in CREATIVE_SYSTEM_PROMPT
    assert "Return exactly one visual observation" in CREATIVE_SYSTEM_PROMPT
