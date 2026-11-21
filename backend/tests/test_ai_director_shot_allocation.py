"""Focused regression for the historical four-cut vs long-hold comparison."""

import pytest

from libtv_agent.services.ai_creative_planner import (
    CREATIVE_SYSTEM_PROMPT,
    _allocate_shot_windows,
    compile_creative_variants,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


def test_ten_second_explicit_cuts_avoid_overlong_windows_without_inventing_shots():
    windows = _allocate_shot_windows(
        [2.5, 3.0, 4.5], duration_seconds=10, cut_mode="explicit_cuts",
    )
    assert sum(windows) == pytest.approx(10)
    assert len(windows) == 3
    assert windows[0] == pytest.approx(2.5)  # Keep the hook short.
    assert max(windows) <= 3.75 + 1e-6
    assert min(windows) > 0
    # A 2s hook remains 2s even when other editorial windows need 4s.
    alternative = _allocate_shot_windows(
        [2.0, 3.5, 4.5], duration_seconds=10, cut_mode="explicit_cuts",
    )
    assert alternative == pytest.approx([2.0, 4.0, 4.0])


def test_ten_second_continuous_and_two_shot_plans_keep_ai_weighting():
    assert _allocate_shot_windows(
        [2.5, 3.0, 4.5], duration_seconds=10, cut_mode="single_take",
    ) == pytest.approx([2.5, 3.0, 4.5])
    assert _allocate_shot_windows(
        [5.5, 4.5], duration_seconds=10, cut_mode="explicit_cuts",
    ) == pytest.approx([5.5, 4.5])
    assert _allocate_shot_windows(
        [2.5, 3.0, 4.5], duration_seconds=15, cut_mode="explicit_cuts",
    ) == pytest.approx([3.75, 4.5, 6.75])


def test_compiler_preserves_beat_actions_and_product_proof_with_new_timing():
    original = fixture_solo_plan().variants[0]
    selected = original.beats[:3]
    beats = [
        beat.model_copy(update={"duration_weight": weight})
        for beat, weight in zip(selected, [2.5, 3.0, 4.5], strict=True)
    ]
    batch = type(fixture_solo_plan())(variants=[original.model_copy(update={"beats": beats})])
    compiled = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=batch,
        storyboard_count=1,
        variant_count=1,
        duration_seconds=10,
        reference_role_prompt=(
            "References: @image_1 anchors actor and original scene. "
            "@image_2 is the primary product reference."
        ),
        garment_material="sweatshirt",
        cut_mode="explicit_cuts",
    )[0]
    assert len(compiled.beats) == 3
    assert compiled.beats[-1]["endSeconds"] == 10
    assert all(
        beat["endSeconds"] - beat["startSeconds"] <= 3.8
        for beat in compiled.beats
    )
    for source, rendered in zip(beats, compiled.beats, strict=True):
        assert rendered["action"] == source.action
        assert rendered["product_proof"] == source.product_proof
    assert "@image_2" in compiled.prompt
    assert "reading-ghost" in compiled.prompt
    assert "connected, purposeful" in CREATIVE_SYSTEM_PROMPT
