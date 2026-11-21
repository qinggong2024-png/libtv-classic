"""Contract tests for grounding AI video actions in exact storyboard frames."""

import pytest
from libtv_agent.services.creative_reference_guard import (
    CreativeBeat,
    ReferenceGroundingError,
    StoryboardReferenceFacts,
    validate_reference_bound_plan,
)

SOLO = StoryboardReferenceFacts(
    frame_order=1, visible_person_ids=("model-1",), verified_person_count=True,
    visible_prop_ids=("cup",), scene_id="bookstore",
)
PAIR = StoryboardReferenceFacts(
    frame_order=2, visible_person_ids=("model-1", "friend-2"),
    verified_person_count=True, scene_id="living-room",
)


def test_solo_visible_product_action_is_allowed():
    validate_reference_bound_plan(
        [SOLO],
        [CreativeBeat(frame_order=1, action="She shifts her weight naturally",
                      actor_ids=("model-1",), used_prop_ids=("cup",))],
    )


@pytest.mark.parametrize(
    "beat",
    [
        CreativeBeat(frame_order=1, action="Friend reacts", actor_ids=("model-1",)),
        CreativeBeat(frame_order=1, action="Turn to another person", actor_ids=("model-1",)),
        CreativeBeat(frame_order=1, action="朋友看到衣服以后惊讶", actor_ids=("model-1",)),
        CreativeBeat(frame_order=1, action="She turns toward him",
                     actor_ids=("model-1",), interaction_partner_ids=("friend-2",)),
        CreativeBeat(frame_order=1, action="A stranger enters", actor_ids=("model-1",),
                     introduces_person=True),
        CreativeBeat(frame_order=1, action="A voice reacts off-camera",
                     actor_ids=("model-1",), requires_offscreen_person=True),
        CreativeBeat(frame_order=1, action="She picks up a book",
                     actor_ids=("model-1",), used_prop_ids=("book",)),
        CreativeBeat(frame_order=1, action="Cut to a cafe", actor_ids=("model-1",),
                     introduces_scene=True),
        CreativeBeat(frame_order=1, action="She replies", actor_ids=("model-1",),
                     speaker_id="model-1"),
    ],
)
def test_solo_frame_rejects_unsupported_entities_and_audio(beat):
    with pytest.raises(ReferenceGroundingError):
        validate_reference_bound_plan([SOLO], [beat])


@pytest.mark.parametrize(
    "action",
    [
        "He adjusts his sweatshirt and returns to his book",
        "A man turns around to show the original back print",
        "A woman opens her paperback and smiles to herself",
        "She wears the quote about friends printed on the sweatshirt",
    ],
)
def test_solo_reference_does_not_confuse_normal_pronouns_or_gender_with_extra_actor(action):
    validate_reference_bound_plan(
        [SOLO], [CreativeBeat(
            frame_order=1, action=action, actor_ids=("model-1",),
        )],
    )


@pytest.mark.parametrize(
    "action",
    [
        "他向镜头迈出半步，展示胸前印花",
        "她用另一只手轻轻整理卫衣下摆",
        "She uses another hand to flatten the garment",
        "He walks toward the camera while showing the sweatshirt print",
    ],
)
def test_solo_actor_own_two_hands_and_chinese_motion_are_not_second_people(action):
    validate_reference_bound_plan(
        [SOLO],
        [CreativeBeat(frame_order=1, action=action, actor_ids=("model-1",))],
    )


@pytest.mark.parametrize(
    "action",
    [
        "Another hand enters from offscreen to adjust the shirt",
        "画外另一只手伸入画面并拉动衣角",
        "她向他走去，两人准备合影",
        "A different person enters frame and touches the model",
    ],
)
def test_solo_guard_still_rejects_explicit_other_people_or_external_hands(action):
    with pytest.raises(ReferenceGroundingError, match="implied second person"):
        validate_reference_bound_plan(
            [SOLO],
            [CreativeBeat(frame_order=1, action=action, actor_ids=("model-1",))],
        )


def test_pair_interaction_is_allowed_only_on_frame_with_two_visible_people():
    validate_reference_bound_plan(
        [SOLO, PAIR],
        [CreativeBeat(frame_order=2, action="She reacts to his expression",
                      actor_ids=("model-1",), interaction_partner_ids=("friend-2",))],
    )


def test_pair_from_other_frame_cannot_be_borrowed_into_solo_frame():
    with pytest.raises(ReferenceGroundingError):
        validate_reference_bound_plan(
            [SOLO, PAIR],
            [CreativeBeat(frame_order=1, action="She reacts",
                          actor_ids=("model-1",), interaction_partner_ids=("friend-2",))],
        )


def test_uncertain_count_allows_a_camera_beat_without_new_actors():
    reference = StoryboardReferenceFacts(
        frame_order=1, visible_person_ids=(), verified_person_count=False
    )
    validate_reference_bound_plan(
        [reference], [CreativeBeat(frame_order=1, action="Pan across the sweater")]
    )


def test_uncertain_count_still_cannot_invent_a_second_person():
    reference = StoryboardReferenceFacts(
        frame_order=1, visible_person_ids=("p1",), verified_person_count=False
    )
    with pytest.raises(ReferenceGroundingError, match="unverified person"):
        validate_reference_bound_plan(
            [reference], [CreativeBeat(
                frame_order=1, action="She talks to the friend",
                actor_ids=("p1",), interaction_partner_ids=("p2",),
            )]
        )


def test_product_only_verified_frame_can_have_product_camera_beat():
    reference = StoryboardReferenceFacts(
        frame_order=1, visible_person_ids=(), verified_person_count=True
    )
    validate_reference_bound_plan(
        [reference], [CreativeBeat(frame_order=1, action="Show the garment on the table")]
    )


def test_missing_reference_frame_is_rejected():
    with pytest.raises(ReferenceGroundingError, match="nonexistent"):
        validate_reference_bound_plan(
            [SOLO], [CreativeBeat(frame_order=2, action="Camera pushes in")]
        )


def test_distinct_speaker_requires_audio_enabled_and_visible_actor():
    with pytest.raises(ReferenceGroundingError, match="dialogue is not enabled"):
        validate_reference_bound_plan(
            [PAIR],
            [CreativeBeat(frame_order=2, action="She says the line",
                          actor_ids=("model-1",), speaker_id="model-1")],
        )
    validate_reference_bound_plan(
        [PAIR],
        [CreativeBeat(frame_order=2, action="She says the line",
                      actor_ids=("model-1",), speaker_id="model-1")],
        audio_enabled=True,
    )


def test_self_interaction_does_not_satisfy_two_person_contract():
    with pytest.raises(ReferenceGroundingError, match="distinct visible"):
        validate_reference_bound_plan(
            [PAIR],
            [CreativeBeat(frame_order=2, action="Interaction",
                          actor_ids=("model-1",), interaction_partner_ids=("model-1",))],
        )
