"""V2.5 opt-in compact wire contract for creative planning.

This is a transport-only representation. Before compilation, convert back into
the original complete typed contract; do not weaken reference-grounding checks.
"""
from __future__ import annotations

from typing import Literal

from libtv_agent.adapters.openai_analysis import ContractModel
from libtv_agent.templates.garment_materials import GarmentEdgeFinish, GarmentMaterial
from pydantic import Field


class WirePerson(ContractModel):
    id: str = Field(min_length=1, max_length=24)
    evidence: str = Field(min_length=1, max_length=140)
    key: str | None = Field(default=None, max_length=24)
    match: str = Field(default="", max_length=140)


class WireProp(ContractModel):
    id: str = Field(min_length=1, max_length=24)
    evidence: str = Field(min_length=1, max_length=140)


class WireFrame(ContractModel):
    f: int = Field(ge=1, le=7)
    counted: bool
    people: list[WirePerson] = Field(max_length=5)
    props: list[WireProp] = Field(max_length=15)
    scene: str = Field(max_length=150)
    pose: str = Field(max_length=150)
    crop: str = Field(max_length=140)
    light: str = Field(max_length=120)
    garment: str = Field(max_length=220)
    space: str = Field(max_length=190)


class WireShot(ContractModel):
    f: int = Field(ge=1, le=7)
    role: Literal["hook", "reveal", "proof", "payoff", "closing"]
    cut: Literal["start", "cut", "reframe", "continuous"]
    weight: float = Field(gt=0, le=20)
    why: str = Field(min_length=1, max_length=110)
    entry: str = Field(min_length=1, max_length=140)
    move: str = Field(min_length=1, max_length=170)
    exit: str = Field(min_length=1, max_length=140)
    cam: str = Field(min_length=1, max_length=140)
    proof: str = Field(min_length=1, max_length=160)
    actors: list[str] = Field(max_length=5)
    partners: list[str] = Field(max_length=5)
    props: list[str] = Field(max_length=15)
    novelty: Literal["none", "person", "prop", "scene", "offscreen", "voice"]


class WireVariant(ContractModel):
    # Preserve the complete five-shot I creative rationale from the validated wire.
    idea: str = Field(min_length=1, max_length=300)
    hook: str = Field(min_length=1, max_length=140)
    reason: str = Field(min_length=1, max_length=180)
    video_prompt: str = Field(default="", max_length=1600)
    shots: list[WireShot] = Field(min_length=1, max_length=10)


class WireCreativeResponse(ContractModel):
    frames: list[WireFrame] = Field(min_length=1, max_length=7)
    product: str = Field(max_length=350)
    fabric: GarmentMaterial = "unknown"
    confidence: Literal["high", "uncertain"] = "uncertain"
    fabric_evidence: str = Field(default="", max_length=180)
    cuffs: GarmentEdgeFinish = "unknown"
    hem: GarmentEdgeFinish = "unknown"
    edge_evidence: str = Field(default="", max_length=180)
    variants: list[WireVariant] = Field(min_length=1, max_length=4)


WIRE_DIRECTIVE = (
    "V2.5 COMPACT WIRE FORMAT. Follow schema keys and use concise complete fields. "
    "Each shot's novelty is none/person/prop/scene/offscreen/voice, referring only "
    "to an entity NEW and unsupported by that source frame; existing actors, props "
    "and new camera angles are not novel. "
    "Buyer reason: who wants this visible differentiator, for what believable "
    "occasion or identity, and why choose it over a generic garment? "
    "Show the reason through purposeful action and product proof, not just "
    "'looks nice' or 'bold graphic'. Never invent prices, delivery, scarcity "
    "or unsupported fabric benefits. The camera follows the actor. "
    "Write each variant's action narrative in one coherent language (Chinese or English), "
    "keeping @image_N references, literal garment text and user copy unchanged."
)

# Optional USP mode replaces the normal wire directive rather than appending.
# No schema changes, extra model call, or added output tokens are requested.
USP_WIRE_DIRECTIVE = (
    "V2.5 COMPACT WIRE: use complete concise schema fields. "
    "Novelty means only NEW unsupported entities, not existing actors or camera angles. "
    "Hook: product-linked surprise, contrast or reaction within roughly 3s; not a static pose. "
    "Buyer reason: who wants this visible differentiator, for which occasion/identity, "
    "and why choose it over generic clothing? "
    "Show cause -> actor reaction -> product proof through purposeful action and cuts; "
    "camera follows actor. "
    "End on visible product payoff for a separate CTA, not fabricated text. "
    "Never invent price, delivery, scarcity or unsupported benefits. "
    "Silent video: no voices, captions or overlays. "
    "One coherent language (Chinese or English); preserve @image_N references, "
    "printed garment text and user copy."
)


# Applied only to GPT-6.1 Terra V2.5 prompts. No added wire fields or
# post-generation motion rejections; Sol and legacy requests remain unchanged.
TERRA_MOTION_DIRECTIVE = (
    "TERRA ACTION CAUSALITY: Build one evolving human micro-story, not isolated "
    "garment poses. A visible product cue causes the actor's natural reaction "
    "or choice; the next beat carries that behavior forward and the closing finishes "
    "it with fresh product evidence, not a pose reset or repeated print reveal. "
    "For artwork-only proof, emphasize personal expression, not invented comfort, "
    "price or fabric benefits. Keep action size and shot count flexible."
)


def expand_wire_response(response: WireCreativeResponse, *, native_i: bool = False,
                         native_v34: bool = False):
    """Restore the V2.4 model and all safeguards; invented entities fail closed."""
    # Local import prevents a module import cycle.
    from libtv_agent.services.ai_creative_planner import (
        CreativeDirectorResponse,
        PlannedBeat,
        PlannedVariant,
        VisibleReferencePerson,
        VisibleReferenceProp,
        VisualReferenceResult,
        VisualStoryboardFact,
    )

    frames = [
        VisualStoryboardFact(
            frame_order=f.f,
            person_count_verified=f.counted,
            people=[
                VisibleReferencePerson(
                    person_id=p.id, visible_evidence=p.evidence,
                    character_key=p.key, match_evidence=p.match,
                )
                for p in f.people
            ],
            props=[
                VisibleReferenceProp(prop_id=p.id, visible_evidence=p.evidence)
                for p in f.props
            ],
            scene=f.scene, initial_pose=f.pose, framing=f.crop,
            lighting=f.light, garment_evidence=f.garment,
            spatial_constraints=f.space,
        )
        for f in response.frames
    ]
    observations = VisualReferenceResult(
        storyboards=frames, product_appearance=response.product,
        material_type=response.fabric,
        material_confidence=response.confidence,
        material_evidence=response.fabric_evidence,
        cuff_finish=response.cuffs, hem_finish=response.hem,
        edge_evidence=response.edge_evidence,
    )
    from libtv_agent.services.native_five_shot import known_animal_ids
    frames_by_order = {f.f: f for f in response.frames}
    variants = []
    for variant in response.variants:
        shots = []
        for shot in variant.shots:
            novelty = shot.novelty
            observed_animals = (known_animal_ids(frames_by_order.get(shot.f))
                                if (native_i or native_v34) else set())
            animal_partner_ids = [x for x in shot.partners if x in observed_animals]
            human_partner_ids = [x for x in shot.partners if x not in observed_animals]
            seen_props = list(dict.fromkeys([*shot.props, *animal_partner_ids]))
            shots.append(PlannedBeat(
                frame_order=shot.f, shot_role=shot.role,
                transition=shot.cut, duration_weight=shot.weight,
                purpose=shot.why, entry_state=shot.entry,
                action=shot.move, exit_state=shot.exit,
                camera=shot.cam, product_proof=shot.proof,
                actor_ids=shot.actors,
                interaction_partner_ids=human_partner_ids,
                used_prop_ids=seen_props,
                speaker_id="unsupported" if novelty == "voice" else None,
                requires_offscreen_person=novelty == "offscreen",
                introduces_person=novelty == "person",
                introduces_prop=novelty == "prop",
                introduces_scene=novelty == "scene",
            ))
        variants.append(PlannedVariant(
            creative_idea=variant.idea,
            visual_hook=variant.hook,
            buyer_reason=variant.reason,
            native_video_prompt=variant.video_prompt if (native_i or native_v34) else "",
            beats=shots,
        ))
    return CreativeDirectorResponse(observations=observations, variants=variants)
