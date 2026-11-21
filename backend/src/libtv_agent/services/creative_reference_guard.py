"""Reference-grounding guard for AI-created video beats.

This module does not infer visual facts. A vision adapter must supply verified facts
from the exact source storyboard frame. It deliberately fails closed when character
evidence is missing rather than silently allowing invented participants.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


class ReferenceGroundingError(ValueError):
    """The creative plan contains facts unsupported by its reference image."""


@dataclass(frozen=True)
class StoryboardReferenceFacts:
    frame_order: int
    visible_person_ids: tuple[str, ...]
    verified_person_count: bool
    visible_prop_ids: tuple[str, ...] = ()
    scene_id: str | None = None


@dataclass(frozen=True)
class CreativeBeat:
    frame_order: int
    action: str
    actor_ids: tuple[str, ...] = ()
    interaction_partner_ids: tuple[str, ...] = ()
    used_prop_ids: tuple[str, ...] = ()
    speaker_id: str | None = None
    requires_offscreen_person: bool = False
    introduces_person: bool = False
    introduces_prop: bool = False
    introduces_scene: bool = False


# Text is a defense-in-depth check, not a replacement for a typed AI output
# schema. The eventual renderer must only consume validated structured beats.
_UNSEEN_PERSON_SUGGESTIONS = re.compile(
    r"\bfriend(?:s)?\b(?!\s+(?:printed|graphic|text|on\b))|"
    r"\b(?:companion|another person|second person|"
    r"someone else|another woman|another man|stranger|coworker|"
    r"passerby|passer-by|other people|different person|other person|someone|extra actor)\b|"
    r"\b(?:to|toward|towards|at)\s+him\b|"
    r"朋友|同伴|旁人|另一个人|第二个人|路人|同事|"
    r"向他|对他说|看向他|旁边的人|画外人声|画外朋友",
    re.IGNORECASE,
)

# "Her other hand" or "他向镜头" are ordinary one-person movements.
# A *separately entering* hand is still evidence of an unsupported actor.
_EXTERNAL_HAND_SUGGESTIONS = re.compile(
    r"\banother hand\b(?=[^.!?\n]{0,45}\b(?:enters?|appears?|"
    r"comes?\s+into(?:\s+the)?\s+frame|"
    r"reaches?\s+in|from\s+off[- ]?(?:screen|camera))\b)|"
    r"\b(?:off[- ]?(?:screen|camera))\s+hand\b|"
    r"(?:画外|镜头外|别人|另一人).{0,12}(?:手|手臂)|"
    r"另一只手.{0,12}(?:从画外|从镜头外|伸入画面|伸进镜头)",
    re.IGNORECASE,
)


def validate_reference_bound_plan(
    references: Sequence[StoryboardReferenceFacts],
    beats: Sequence[CreativeBeat],
    *,
    audio_enabled: bool = False,
) -> None:
    """Reject unsupported people, dialogue, props or locations before generation.

    Every beat is grounded in its identified storyboard image.
    Unknown person counts must not block creative movement or camera exploration;
    referenced actor IDs must still be actually observed in that frame.
    """
    if not references:
        raise ReferenceGroundingError("no storyboard references")
    if not beats:
        raise ReferenceGroundingError("creative plan contains no beats")
    by_order: dict[int, StoryboardReferenceFacts] = {}
    for reference in references:
        if reference.frame_order < 1 or reference.frame_order in by_order:
            raise ReferenceGroundingError("invalid or duplicated reference frame order")
        if len(reference.visible_person_ids) != len(set(reference.visible_person_ids)):
            raise ReferenceGroundingError("duplicate person IDs in reference evidence")
        if len(reference.visible_prop_ids) != len(set(reference.visible_prop_ids)):
            raise ReferenceGroundingError("duplicate prop IDs in reference evidence")
        by_order[reference.frame_order] = reference

    for beat in beats:
        reference = by_order.get(beat.frame_order)
        if reference is None:
            raise ReferenceGroundingError(
                f"beat uses nonexistent reference frame {beat.frame_order}"
            )
        known_people = set(reference.visible_person_ids)
        known_props = set(reference.visible_prop_ids)
        used_people = set((*beat.actor_ids, *beat.interaction_partner_ids))
        if used_people - known_people:
            raise ReferenceGroundingError(
                f"beat introduces an unverified person in frame {beat.frame_order}"
            )
        if set(beat.used_prop_ids) - known_props:
            raise ReferenceGroundingError(
                f"beat introduces an unverified prop in frame {beat.frame_order}"
            )
        if (
            beat.requires_offscreen_person
            or beat.introduces_person
            or beat.introduces_prop
            or beat.introduces_scene
        ):
            raise ReferenceGroundingError(
                "new people, offscreen people, props and locations are prohibited"
            )
        if beat.interaction_partner_ids:
            if not beat.actor_ids or not (
                set(beat.interaction_partner_ids) - set(beat.actor_ids)
            ):
                raise ReferenceGroundingError(
                    "person-to-person interaction requires a distinct visible actor"
                )
        if beat.speaker_id is not None:
            if not audio_enabled:
                raise ReferenceGroundingError("dialogue is not enabled for this video")
            if beat.speaker_id not in known_people or beat.speaker_id not in beat.actor_ids:
                raise ReferenceGroundingError("speaker is not a visible acting person")
        if not known_people and re.search(
            r"\b(she|her|he|him|person|woman|man|model|actor|hands?)\b|"
            r"她|他|模特|人物|人手|双手", beat.action, re.IGNORECASE,
        ):
            raise ReferenceGroundingError("product-only frame cannot contain person actions")
        if len(known_people) <= 1:
            person_match = _UNSEEN_PERSON_SUGGESTIONS.search(beat.action)
            external_hand = _EXTERNAL_HAND_SUGGESTIONS.search(beat.action)
            if person_match or external_hand:
                # Diagnostic category only: do not log the creative text or
                # product graphics/quotes included in the action narrative.
                signal = "person_reference" if person_match else "external_hand"
                raise ReferenceGroundingError(
                    "solo/product frame contains an implied second person: "
                    f"{beat.frame_order}; signal={signal}"
                )
