"""Offline regression for motion intent and independent camera energy.

No external AI or video-provider request is made by these tests.
"""

import json
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_PLANNER_VERSION,
    CREATIVE_SYSTEM_PROMPT,
    CreativeDirectorResponse,
    PlannedVariantBatch,
    compile_creative_variants,
    director_actor_motion_guidance,
    plan_creative_video,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


class RecordedPlanner:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=CreativeDirectorResponse(
            observations=fixture_solo_facts(),
            variants=fixture_solo_plan().variants,
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intensity", "expected"),
    [
        ("low", "Subtle but expressive"),
        ("standard", "moderate body movement"),
        ("high", "Lively full-body movement"),
    ],
)
async def test_motion_directive_reaches_existing_single_multimodal_call(
    intensity, expected,
):
    mock = RecordedPlanner()
    result = await plan_creative_video(
        settings=Settings(app_env="test", openai_api_key=None, _env_file=None),
        storyboard_images=[
            AnalysisImageInput("scene", 1, "data:image/png;base64,AAA")
        ],
        product_images=[
            AnalysisImageInput("product", 1, "data:image/png;base64,BBB")
        ],
        reference_role_prompt="References: @image_1 actor; @image_2 garment",
        creative_brief="Show the front art naturally",
        brand_tone="Everyday UGC",
        duration_seconds=10,
        variant_count=1,
        action_intensity=intensity,
        client=SimpleNamespace(responses=mock),
    )
    assert len(mock.calls) == 1
    call = mock.calls[0]
    assert "connected, purposeful real-life behavior" in call["input"][0]["content"]
    assert "NOT forbidden motion keywords" in call["input"][0]["content"]
    request = json.loads(call["input"][1]["content"][0]["text"])
    directive = request["creativeDirective"]
    assert request["actionIntensity"] == intensity
    assert expected in directive
    assert "fast push-ins" in directive
    assert "purposeful cuts" in directive
    assert "fit the source pose" in directive
    assert "context-driven sitting" in directive
    assert "EXACTLY 10s" in directive
    assert result[0].planner_version == CREATIVE_PLANNER_VERSION
    assert len(result[0].beats) == 4
    assert len(result[0].prompt) <= 3072


def test_body_motion_and_camera_energy_are_separate_preferences():
    assert "intensity of ACTOR movement, independent of camera speed" in (
        CREATIVE_SYSTEM_PROMPT
    )
    for intensity in ("low", "standard", "high"):
        guidance = director_actor_motion_guidance(intensity)
        assert "fast push-ins" in guidance
        assert "hard garment stretch" in guidance
        assert "remain valid" in guidance
    assert CREATIVE_PLANNER_VERSION.endswith("v2.4.3-evidence-paced")


def test_examples_steer_ai_without_new_hard_rejection_gate():
    """A legacy poorly motivated beat remains compileable for offline replay.

    The new creative guidance should influence new plans, not block or silently
    rewrite historical plans based on a keyword blacklist.
    """
    observations = fixture_solo_facts()
    good = fixture_solo_plan().variants[0]
    awkward_beat = good.beats[-1].model_copy(update={
        "action": (
            "She abruptly half-squats, raises an arm to expose her underarm, "
            "then yanks the sweatshirt hem."
        ),
    })
    awkward = good.model_copy(update={
        "beats": [*good.beats[:-1], awkward_beat],
    })
    for candidate in (good, awkward):
        compiled = compile_creative_variants(
            observations=observations,
            batch=PlannedVariantBatch(variants=[candidate]),
            storyboard_count=1,
            variant_count=1,
            duration_seconds=10,
            reference_role_prompt="References: @image_1 actor, @image_2 product",
        )[0]
        assert len(compiled.beats) == 4
        assert len(compiled.prompt) <= 3072
    assert "half-squats" in compiled.prompt
