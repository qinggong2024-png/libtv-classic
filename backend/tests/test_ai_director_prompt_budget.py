"""Keep AI director instructions focused while preserving essential controls."""

from libtv_agent.services.ai_creative_planner import (
    CREATIVE_SYSTEM_PROMPT,
    director_actor_motion_guidance,
    director_duration_guidance,
)


def test_director_prompt_priority_and_length_budget():
    prompt = CREATIVE_SYSTEM_PROMPT
    assert len(prompt) < 5000  # Prevent cumulative instruction creep.
    assert prompt.index("EVIDENCE AND CONTINUITY") < prompt.index("SELLING IDEA")
    assert prompt.index("SELLING IDEA") < prompt.index("NATURAL PACE")
    assert prompt.index("NATURAL PACE") < prompt.index("STRUCTURED OUTPUT")
    assert "normal real-life speed" in prompt
    assert "no 2-second cutting rule" in prompt
    assert "record visual facts" in prompt
    assert "This workflow is silent" in prompt


def test_dynamic_directives_only_add_task_specific_pacing():
    for intensity in ("low", "standard", "high"):
        directive = director_actor_motion_guidance(intensity)
        assert len(directive) < 550
        assert "Shot duration weights only set edit windows" in directive
    for seconds in (5, 10, 15):
        directive = director_duration_guidance(seconds)
        assert len(directive) < 340
        assert f"EXACTLY {seconds}s" in directive
