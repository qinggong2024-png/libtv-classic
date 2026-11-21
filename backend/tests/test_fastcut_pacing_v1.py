"""10-second fastcut planning guardrails; provider and CUT behavior stay unchanged."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    director_duration_guidance,
    plan_creative_video,
)
from libtv_agent.services.fastcut_pacing import (
    PROFILE,
    REFERENCE_SHOT_DURATIONS,
    director_guidance,
    eligible,
)
from test_ai_director_material_duration import OneCallResponse


def test_approved_editorial_rhythm_is_exactly_ten_seconds():
    assert PROFILE == "allwe-ugc-five-shot-pacing-v1"
    assert len(REFERENCE_SHOT_DURATIONS) == 5
    assert sum(REFERENCE_SHOT_DURATIONS) == pytest.approx(10.0)
    assert min(REFERENCE_SHOT_DURATIONS) >= 1.2
    assert "do not stack sub-second cuts" in director_guidance()


@pytest.mark.parametrize("enabled,seconds,cut_mode,expected", [
    (False,10,"explicit_cuts",False),
    (True,10,"explicit_cuts",True),
    (True,10,"continuous_when_possible",False),
    (True,15,"explicit_cuts",False),
    (True,20,"explicit_cuts",False),
])
def test_opt_in_is_narrow(enabled,seconds,cut_mode,expected):
    assert eligible(enabled=enabled,duration_seconds=seconds,cut_mode=cut_mode) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds,cut_mode,expected", [
    (10,"explicit_cuts",True),
    (10,"continuous_when_possible",False),
    (15,"explicit_cuts",False),
])
async def test_actual_planner_request_only_uses_approved_10s_pacing(seconds,cut_mode,expected):
    fake = OneCallResponse()
    settings=Settings(app_env="test",_env_file=None,
                      fastcut_ugc_pacing_v1_enabled=True)
    await plan_creative_video(
        settings=settings,
        storyboard_images=[AnalysisImageInput("one",1,"data:image/png;base64,AAA")],
        product_images=[AnalysisImageInput("two",1,"data:image/png;base64,BBB")],
        reference_role_prompt="References @image_1 storyboard; @image_2 product",
        creative_brief="No unsupported new details",
        brand_tone="casual",
        duration_seconds=seconds,
        variant_count=1,
        cut_mode=cut_mode,
        client=SimpleNamespace(responses=fake),
    )
    assert len(fake.calls)==1
    req=json.loads(fake.calls[0]["input"][1]["content"][0]["text"])
    assert ("APPROVED ALLWE 10s FASTCUT" in req["creativeDirective"]) is expected
    if expected:
        assert "different product evidence" in req["creativeDirective"].lower()
        assert "do not stack sub-second cuts" in req["creativeDirective"]


def test_legacy_guidance_unchanged_when_disabled():
    assert "Never impose four cuts" in director_duration_guidance(10)
    assert "APPROVED ALLWE" not in director_duration_guidance(10)
    assert "APPROVED ALLWE" in director_duration_guidance(10,fastcut=True)
