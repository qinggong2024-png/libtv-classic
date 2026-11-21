"""User-selected duration and reference/brief-driven garment construction tests.

All tests run offline: no external AI call and no paid video submission.
"""
import json
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_SYSTEM_PROMPT,
    CreativeDirectorResponse,
    PlannedVariantBatch,
    compile_creative_variants,
    director_duration_guidance,
    plan_creative_video,
)
from libtv_agent.templates.garment_materials import (
    confirmed_edge_finishes,
    garment_edge_negative_guidance,
    infer_garment_material,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


def compile_reference(*, seconds=10, facts=None, brief="", material="unknown", beats=None):
    facts = facts or fixture_solo_facts()
    plan = fixture_solo_plan()
    if beats is not None:
        first = plan.variants[0].model_copy(update={"beats": beats})
        plan = PlannedVariantBatch(variants=[first])
    return compile_creative_variants(
        observations=facts, batch=plan,
        storyboard_count=1, variant_count=1, duration_seconds=seconds,
        reference_role_prompt="References: @image_1 person, @image_2 product",
        creative_brief=brief,
        garment_material=material,
    )[0]


@pytest.mark.parametrize(
    ("material_text", "expected"),
    [
        ("ordinary sweater", "unknown"),
        ("oversized cardigan", "unknown"),
        ("短马海毛普通毛衣", "short_mohair"),
        ("短马海毛开衫", "short_mohair_cardigan"),
        ("纯棉短袖", "cotton_tee"),
        ("网眼球衣", "mesh_jersey"),
    ],
)
def test_generic_knit_does_not_invent_short_mohair(material_text, expected):
    assert infer_garment_material(material_text) == expected


@pytest.mark.parametrize("brief", [
    "袖口和下摆不是罗纹的，材质短马海毛",
    "袖口和下摆没有螺纹",
    "no ribbed cuffs and hem",
])
def test_explicit_user_correction_generates_both_negative_prompts(brief):
    assert confirmed_edge_finishes(brief) == ("non_ribbed", "non_ribbed")
    compiled = compile_reference(brief=brief, material="short_mohair")
    assert "NO ribbed cuffs" in compiled.prompt
    assert "NO ribbed hem" in compiled.prompt
    assert len(compiled.prompt) <= 3072


def test_only_confirmed_cuff_is_constrained():
    assert confirmed_edge_finishes("袖口没有罗纹；其他部位按商品图") == (
        "non_ribbed", "unknown",
    )
    assert "NO ribbed cuffs" in garment_edge_negative_guidance("non_ribbed", "unknown")
    assert "NO ribbed hem" not in garment_edge_negative_guidance("non_ribbed", "unknown")


def test_ambiguous_reference_never_forces_non_ribbed_edges_or_mohair():
    compiled = compile_reference()
    assert "NO ribbed cuffs" not in compiled.prompt
    assert "NO ribbed hem" not in compiled.prompt
    assert "mohair-style" not in compiled.prompt


def test_confident_product_image_evidence_guides_material_and_smooth_edges():
    evidence = fixture_solo_facts().model_copy(update={
        "material_type": "short_mohair",
        "material_confidence": "high",
        "material_evidence": "short dense pile clearly visible on product photo",
        "cuff_finish": "non_ribbed",
        "hem_finish": "non_ribbed",
        "edge_evidence": "plain smooth edge on both cuff and lower hem",
    })
    compiled = compile_reference(facts=evidence)
    assert "short mohair-style" in compiled.prompt or "brushed mohair-style" in compiled.prompt
    assert "NO ribbed cuffs" in compiled.prompt
    assert "NO ribbed hem" in compiled.prompt


def test_low_confidence_visual_inference_does_not_claim_material_or_edge_finish():
    evidence = fixture_solo_facts().model_copy(update={
        "material_type": "short_mohair",
        "material_confidence": "uncertain",
        "material_evidence": "maybe fluffy",
        "cuff_finish": "non_ribbed",
        "hem_finish": "non_ribbed",
        "edge_evidence": "",
    })
    compiled = compile_reference(facts=evidence)
    assert "mohair-style" not in compiled.prompt
    assert "NO ribbed cuffs" not in compiled.prompt


def test_user_confirmed_edge_overrides_conflicting_vision():
    evidence = fixture_solo_facts().model_copy(update={
        "cuff_finish": "ribbed",
        "hem_finish": "ribbed",
        "edge_evidence": "visible edge texture, could be mistaken",
    })
    compiled = compile_reference(
        facts=evidence, brief="实际商品袖口和下摆不是罗纹的",
    )
    assert "NO ribbed cuffs" in compiled.prompt
    assert "NO ribbed hem" in compiled.prompt


@pytest.mark.parametrize("seconds", [1, 4, 5, 8, 10, 12, 15])
def test_compiler_timeline_exactly_matches_user_selected_duration(seconds):
    compiled = compile_reference(seconds=seconds)
    assert compiled.prompt.startswith("References:")
    assert f"{seconds}s 9:16" in compiled.prompt
    assert compiled.beats[0]["startSeconds"] == 0
    assert compiled.beats[-1]["endSeconds"] == seconds
    assert len(compiled.prompt) <= 3072


def test_short_duration_can_use_only_a_subset_of_ordered_references():
    facts = fixture_solo_facts()
    extra = facts.storyboards[0].model_copy(update={"frame_order": 2})
    facts.storyboards.append(extra)
    compiled = compile_creative_variants(
        observations=facts,
        batch=PlannedVariantBatch(variants=[
            fixture_solo_plan().variants[0].model_copy(
                update={"beats": fixture_solo_plan().variants[0].beats[:1]}
            ),
        ]),
        storyboard_count=2,
        variant_count=1,
        duration_seconds=4,
        reference_role_prompt="References: @image_1 @image_2",
    )[0]
    assert len(compiled.beats) == 1
    assert compiled.beats[0]["endSeconds"] == 4


class OneCallResponse:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=CreativeDirectorResponse(
            observations=fixture_solo_facts(),
            variants=fixture_solo_plan().variants,
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [5, 10, 15])
async def test_one_model_call_receives_actual_duration_and_user_edge_correction(seconds):
    fake = OneCallResponse()
    result = await plan_creative_video(
        settings=Settings(app_env="test", openai_api_key=None, _env_file=None),
        storyboard_images=[AnalysisImageInput("one", 1, "data:image/png;base64,AAA")],
        product_images=[AnalysisImageInput("two", 1, "data:image/png;base64,BBB")],
        reference_role_prompt="References: @image_1, product @image_2",
        creative_brief="袖口和下摆不是罗纹的",
        brand_tone="normal",
        duration_seconds=seconds,
        variant_count=1,
        client=SimpleNamespace(responses=fake),
    )
    assert len(fake.calls) == 1
    request = json.loads(fake.calls[0]["input"][1]["content"][0]["text"])
    assert request["durationSeconds"] == seconds
    assert f"EXACTLY {seconds}s" in request["creativeDirective"]
    assert "not a rejection threshold" in request["creativeDirective"]
    assert request["userConfirmedEdgeFinishes"] == {"cuffs": "non_ribbed", "hem": "non_ribbed"}
    assert "NO ribbed cuffs" in result[0].prompt
    assert "NO ribbed hem" in result[0].prompt
    assert result[0].beats[-1]["endSeconds"] == seconds


def test_duration_guidance_never_hardcodes_four_shots():
    assert "four cuts" in director_duration_guidance(5)
    assert "five-second" not in director_duration_guidance(15)
    assert "exact requested durationSeconds" in CREATIVE_SYSTEM_PROMPT


def test_mixed_user_edge_correction_does_not_ban_confirmed_ribbed_hem():
    assert confirmed_edge_finishes(
        "袖口没有罗纹，下摆保留商品图原本的罗纹"
    ) == ("non_ribbed", "unknown")
    assert confirmed_edge_finishes(
        "No ribbed cuffs; hem is visibly ribbed"
    ) == ("non_ribbed", "unknown")


def test_negative_edge_constraints_survive_provider_prompt_compaction():
    original = fixture_solo_plan().variants[0]
    verbose = [
        beat.model_copy(update={
            "action": (beat.action + " with organic texture and bold garment motion " * 3)[:170],
            "camera": (beat.camera + " while retaining real handheld perspective " * 3)[:140],
            "product_proof": (
                beat.product_proof + " visibly accurate wearable art details " * 3
            )[:160],
        })
        for beat in original.beats
    ]
    compiled = compile_reference(
        brief="袖口和下摆不是罗纹的，严禁凭空添加罗纹",
        material="short_mohair",
        beats=verbose,
    )
    assert "NO ribbed cuffs" in compiled.prompt
    assert "NO ribbed hem" in compiled.prompt
    assert len(compiled.beats) == len(verbose)
    assert len(compiled.prompt) <= 3072
    assert "..." not in compiled.prompt  # No chopped-off action or camera command.


def test_short_mohair_catalog_defaults_to_non_ribbed_edges_when_unseen():
    compiled = compile_reference(material="short_mohair_cardigan")
    assert "NO ribbed cuffs" in compiled.prompt
    assert "NO ribbed hem" in compiled.prompt


def test_clearly_ribbed_product_photo_overrides_catalog_default():
    facts = fixture_solo_facts().model_copy(update={
        "cuff_finish": "ribbed",
        "hem_finish": "ribbed",
        "edge_evidence": "clear rib-knit vertical channels in cuff and hem",
    })
    compiled = compile_reference(facts=facts, material="short_mohair")
    assert "NO ribbed cuffs" not in compiled.prompt
    assert "NO ribbed hem" not in compiled.prompt
