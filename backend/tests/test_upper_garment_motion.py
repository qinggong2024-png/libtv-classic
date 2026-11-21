from dataclasses import replace

import pytest
from libtv_agent.services.shot_motion import plan_shot_motion
from libtv_agent.templates.motion_actions import (
    ACTION_LIBRARY,
    ActionCapability,
    ActionContext,
    action_family,
    compatible_action_ids,
    list_motion_actions,
    select_brand_motion,
    supports_upper_garment,
)
from libtv_agent.templates.site_style_profiles import get_site_style_profile


def test_catalog_matches_upper_garment_scope_without_breaking_historical_ids():
    visible = {action.id: action for action in list_motion_actions()}
    assert len(visible) == 41
    assert "motion_to_rest" in visible
    assert "breath_settle" not in visible  # Internal, posture-neutral fallback.
    assert all(
        action.to_public_dict()["productFocus"] == "upper_garment" for action in visible.values()
    )
    assert all(
        word not in " ".join((action.instruction, action.purpose, *action.requirements))
        for action in visible.values()
        for word in ("裙", "裤", "鞋", "下装")
    )
    assert "裙" not in visible["garment_sway_settle"].instruction
    assert "裤" not in visible["back_walk_settle"].instruction
    assert "平移或推进" not in visible["print_detail_pause"].instruction
    assert "改变扣件开合状态" in visible["fastener_adjust_release"].avoid
    assert "footwear_forward_hold" in ACTION_LIBRARY  # Saved task compatibility.


@pytest.mark.parametrize(
    "action_id",
    [
        "waistline_adjust_release",
        "lower_side_seam_smooth",
        "trouser_cuff_adjust_release",
        "knee_flex_settle",
        "skirt_slit_step_settle",
        "footwear_forward_hold",
        "heel_raise_settle",
        "footwear_glance_return",
        "stair_foot_tap",
        "stair_ascend_settle",
        "stair_descend_settle",
        "seated_footwear_reveal",
        "seated_ankle_shift",
        "companion_side_walk",
        "companion_doorway_pass",
    ],
)
def test_old_and_inherited_actions_cannot_reenter_generation(action_id):
    action = ACTION_LIBRARY[action_id]
    inherited = replace(
        action,
        id="brand_legacy",
        base_action_id=action_id,
        product_focus="upper_garment",
        required_capabilities=(),
    )
    library = {**ACTION_LIBRARY, inherited.id: inherited}
    assert not supports_upper_garment(action, library)
    assert not supports_upper_garment(inherited, library)
    assert (
        compatible_action_ids(
            "ootd_walk",
            (action_id, inherited.id),
            context=ActionContext(confirmed=frozenset(ActionCapability)),
            library=library,
        )
        == ()
    )


def test_custom_action_cannot_disguise_footwear_as_an_upper_garment():
    custom = replace(
        ACTION_LIBRARY["stance_shift"],
        id="custom_shoe",
        required_capabilities=(ActionCapability.FOOTWEAR_VISIBLE,),
    )
    assert not supports_upper_garment(custom, {custom.id: custom})


def test_brand_motion_inherits_family_and_cannot_combine_disjoint_image_evidence():
    primary = replace(
        ACTION_LIBRARY["stance_shift"], id="brand_stance", base_action_id="stance_shift"
    )
    library = {**ACTION_LIBRARY, primary.id: primary}
    assert action_family(primary.id, library=library) == "standing_attitude"
    context = ActionContext(
        confirmed=frozenset(
            {
                ActionCapability.STANDING_POSE,
                ActionCapability.FACE_VISIBLE,
                ActionCapability.FACE_GAZE_ADJUSTABLE,
            }
        ),
        evidence_sets=(
            frozenset({ActionCapability.STANDING_POSE}),
            frozenset({ActionCapability.FACE_VISIBLE, ActionCapability.FACE_GAZE_ADJUSTABLE}),
        ),
    )
    result = select_brand_motion(
        template_id="ootd_walk",
        primary_motion_pool=(primary.id,),
        micro_motion_pool=("controlled_gaze",),
        context=context,
        library=library,
    )
    assert result.primary_action_id == primary.id
    assert result.micro_action_id is None
    _, shot = plan_shot_motion(
        profile=replace(
            get_site_style_profile("pagewings"),
            primary_motion_pool=(primary.id,),
            micro_motion_pool=("controlled_gaze",),
        ),
        template_id="mood_model",
        context=context,
        duration=8,
        variant_index=1,
        library=library,
        template_motion_pool=(),
        template_detail_pool=(),
    )
    assert shot["selectedMicroActionId"] is None


@pytest.mark.parametrize("primary", ["controlled_walk", "three_quarter_turn", "back_reveal"])
def test_walks_turns_and_back_views_finish_without_extra_face_actions(primary):
    context = ActionContext(confirmed=frozenset(ActionCapability))
    result = select_brand_motion(
        template_id="ootd_walk",
        primary_motion_pool=(primary,),
        micro_motion_pool=("controlled_gaze", "hem_adjust"),
        context=context,
    )
    assert result.primary_action_id == primary
    assert result.micro_action_id is None
    _, shot = plan_shot_motion(
        profile=replace(
            get_site_style_profile("pagewings"),
            primary_motion_pool=(primary,),
            micro_motion_pool=("controlled_gaze", "hem_adjust"),
        ),
        template_id="garment_detail" if primary == "back_reveal" else "mood_model",
        context=context,
        duration=10,
        variant_index=1,
        library=None,
        template_motion_pool=(),
        template_detail_pool=(),
    )
    assert shot["selectedActionIds"] == [primary]
