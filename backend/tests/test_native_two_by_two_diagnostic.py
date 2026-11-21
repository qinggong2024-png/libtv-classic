"""Regression for 2 storyboard + 2 product references and five-shot validation.

Uses a fake AI stream only: no remote model calls or video-provider submission.
"""
from __future__ import annotations

import pytest

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import plan_creative_video
from libtv_agent.services.fixed_video_tasks import _reference_role_prompt
from test_ai_creative_v25 import FakeClient, wire_fixture
from test_native_i_release import _native_variant


def _two_by_two_wire():
    wire = wire_fixture()
    first = wire.frames[0].model_copy(deep=True)
    second = wire.frames[0].model_copy(deep=True)
    second.f = 2
    for frame in (first, second):
        for person in frame.people:
            person.key = "same_verified_person"
            person.match = "matching face, garment and hairstyle"
    original = wire.variants[0].shots[0]
    shots = [original.model_copy(deep=True) for _ in range(5)]
    for index, (shot, frame) in enumerate(zip(shots, [1, 1, 2, 2, 2], strict=True)):
        shot.f = frame
        shot.cut = "start" if index == 0 else "cut"
        shot.role = ["hook", "reveal", "proof", "payoff", "closing"][index]
    wire.frames = [first, second]
    wire.variants[0].shots = shots
    wire.variants[0].video_prompt = _native_variant().native_video_prompt
    return wire


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected_shots,expected_cuts",
    [
        ("valid", 5, 4),
        ("missing_hard_cut", 5, 3),
        ("four_shots", 4, 4),
        ("invented_person", 5, 4),
    ],
)
async def test_two_storyboards_two_products_skip_local_creative_review(
    scenario, expected_shots, expected_cuts,
):
    wire = _two_by_two_wire()
    if scenario == "missing_hard_cut":
        wire.variants[0].video_prompt = wire.variants[0].video_prompt.replace(
            "HARD CUT TO", "Camera shifts toward", 1
        )
    elif scenario == "four_shots":
        wire.variants[0].shots = wire.variants[0].shots[:4]
    elif scenario == "invented_person":
        wire.variants[0].shots[0].novelty = "person"
    fake = FakeClient(wire)
    args = dict(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[
            AnalysisImageInput(f"story{i}", i, "data:image/png;base64,AA", "STORYBOARD")
            for i in (1, 2)
        ],
        product_images=[
            AnalysisImageInput(f"product{i}", i, "data:image/png;base64,BB", "PRODUCT")
            for i in (1, 2)
        ],
        reference_role_prompt=_reference_role_prompt(
            storyboard_count=2, subject_image_count=2, element_count=0,
        ),
        creative_brief="Authentic everyday smartphone apparel",
        brand_tone="natural UGC",
        duration_seconds=10,
        variant_count=1,
        experimental_v25=True,
        native_five_shot=True,
        client=fake,
    )
    compiled = await plan_creative_video(**args)
    assert len(compiled) == 1
    assert len(compiled[0].beats) == expected_shots
    assert compiled[0].prompt.count("HARD CUT TO") == expected_cuts
    assert all(f"@image_{i}" in compiled[0].prompt for i in range(1, 5))
    if scenario == "invented_person":
        assert compiled[0].beats[0]["introduces_person"] is True
    assert len(fake.calls) == 1, "Do not issue another billable AI request"
    assert len([part for part in fake.calls[0]["input"][1]["content"] if part["type"] == "input_image"]) == 4
