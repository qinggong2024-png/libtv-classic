import uuid
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.motion_facts import ImageMotionFacts, MaterialObservation
from libtv_agent.api.image_generations import ImageBatchManifest, _expand_manifest
from libtv_agent.domain.enums import VariantKind
from libtv_agent.services.automatic_motion_facts import ObservedMotion
from libtv_agent.services.fixed_video_tasks import _resolve_garment_material
from libtv_agent.services.prompt_compiler import PromptCompileInput, compile_video_plan
from libtv_agent.services.prompt_presets import DEFAULT_PROMPT_PRESET_SPECS
from libtv_agent.services.prompts import TimelineShot, render_prompt
from libtv_agent.templates.garment_materials import (
    SHORT_MOHAIR_IMAGE_PROMPT,
    garment_material_guidance,
    infer_garment_material,
)
from libtv_agent.templates.motion_actions import ActionContext
from libtv_agent.templates.site_style_profiles import get_site_style_profile
from libtv_agent.templates.video_templates import get_video_template


def compile_material(material: str, *, prompt_budget: int = 3072):
    return compile_video_plan(
        PromptCompileInput(
            template=get_video_template("garment_detail"),
            storyboard_count=3,
            result_count=1,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            garment_material=material,
            prompt_budget=prompt_budget,
        )
    )


def test_garment_material_inference_requires_one_unambiguous_category():
    assert infer_garment_material("黑色短马海毛毛衣") == "short_mohair"
    assert infer_garment_material("短马海毛") == "short_mohair"
    assert infer_garment_material("毛衣") == "unknown"
    assert infer_garment_material("Mesh Jersey 网眼款") == "mesh_jersey"
    assert infer_garment_material("棉质连衣裙") == "cotton"
    assert infer_garment_material("短马海毛T恤") == "unknown"
    assert infer_garment_material("短马海毛球衣") == "unknown"
    assert infer_garment_material("毛衣和球衣两个款式") == "unknown"
    assert infer_garment_material("未描述款式") == "unknown"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("长袖短马海毛普通毛衣", "short_mohair"),
        ("oversized pullover sweater", "unknown"),
        ("短马海毛开衫毛衣", "short_mohair_cardigan"),
        ("short mohair cardigan", "short_mohair_cardigan"),
        ("网眼小孔的短袖球衣", "mesh_jersey"),
        ("mesh jersey", "mesh_jersey"),
        ("水洗短袖", "washed_tee"),
        ("washed cotton T-shirt", "washed_tee"),
        ("纯棉短袖", "cotton_tee"),
        ("100% cotton tee", "cotton_tee"),
        ("普通卫衣", "sweatshirt"),
        ("pullover sweatshirt", "sweatshirt"),
        ("普通 T 恤", "unknown"),
        ("毛衣和卫衣", "unknown"),
        ("水洗球衣", "mesh_jersey"),
    ],
)
def test_six_regular_garment_materials_are_recorded(title, expected):
    assert infer_garment_material(title) == expected


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        ("short_mohair", "细密短绒"),
        ("short_mohair_cardigan", "开襟形状"),
        ("mesh_jersey", "细密小孔"),
        ("washed_tee", "自然洗旧层次"),
        ("cotton_tee", "哑光棉针织表面"),
        ("sweatshirt", "真实卫衣针织布面"),
    ],
)
def test_six_materials_surface_guidance_is_independent(kind, detail):
    from libtv_agent.templates.garment_materials import garment_video_material_hint

    material_text = garment_material_guidance(kind)
    assert detail in material_text
    assert garment_video_material_hint(kind)
    assert "商品图" in material_text or "商品参考图" in material_text


def test_cardigan_visual_mohair_is_refined_by_explicit_product_title():
    observations = [ObservedMotion(garment_material="short_mohair")]
    resolved, source = _resolve_garment_material(
        observations,
        title="短马海毛开衫",
        creative_brief="",
        ordered_inputs=[],
        ordered_subject_inputs=[],
        by_id={},
    )
    assert (resolved, source) == ("short_mohair_cardigan", "automatic_vision_and_task_text")


def test_short_mohair_prompt_uses_the_two_part_material_description():
    prompt = compile_material("short_mohair").master_prompt

    assert "衣服是短马海毛材质。" in prompt
    assert "领口、袖口、下摆和有无罗纹严格以商品参考图为准" in prompt
    assert "不得自动增删罗纹或加长绒毛" in prompt
    assert "没有螺纹" not in prompt
    assert "不得变成" not in prompt
    assert "网眼" not in prompt
    assert len(prompt) <= 3072


def test_mesh_jersey_prompt_preserves_holes_prints_and_lightweight_drape():
    prompt = compile_material("mesh_jersey").master_prompt

    assert "保持印花、号码、文字和结构一致" in prompt
    assert "网眼、轻薄感与垂坠自然" in prompt
    assert "不得变成平滑棉布" not in prompt
    assert "短绒、厚薄" not in prompt


def test_unknown_material_never_claims_an_unobserved_fabric():
    prompt = compile_material("unknown").master_prompt

    assert "参考商品图，保持印花、款式、文字、材质和结构一致" in prompt
    assert "短绒、厚薄" not in prompt
    assert "网眼" not in prompt


def test_material_guidance_survives_prompt_compression():
    compiled = compile_material("short_mohair", prompt_budget=960)

    assert compiled.variant_seed["promptCompressionLevel"] in {
        "full",
        "compact-style",
        "compact-quality",
    }
    assert "衣服是短马海毛材质。" in compiled.master_prompt
    assert "领口、袖口、下摆和有无罗纹严格以商品参考图为准" in compiled.master_prompt
    assert len(compiled.master_prompt) <= 960


def test_material_and_action_modules_are_independently_replaceable():
    mohair = compile_material("short_mohair")
    mesh = compile_material("mesh_jersey")
    cotton = compile_material("cotton")

    assert (
        mohair.variant_seed["selectedActionIds"]
        == mesh.variant_seed["selectedActionIds"]
        == cotton.variant_seed["selectedActionIds"]
    )
    assert "衣服是短马海毛材质。" in mohair.master_prompt
    assert "领口、袖口、下摆和有无罗纹严格以商品参考图为准" in mohair.master_prompt
    assert "短绒、厚薄" not in mesh.master_prompt
    assert "网眼、轻薄感与垂坠自然" in mesh.master_prompt
    assert "棉布纹理、褶皱与垂坠自然" in cotton.master_prompt
    assert mohair.master_prompt.index("保持：") < mohair.master_prompt.index("服装：")
    assert mohair.master_prompt.index("服装：") < mohair.master_prompt.index("动作：")
    assert mohair.master_prompt.index("动作：") < mohair.master_prompt.index("真实成像：")


def test_ootd_prompt_only_mentions_short_mohair_for_sweaters():
    def compile_ootd(material: str):
        return compile_video_plan(
            PromptCompileInput(
                template=get_video_template("ootd_walk"),
                storyboard_count=3,
                result_count=1,
                action_intensity="standard",
                camera_tempo="standard",
                cut_mode="explicit_cuts",
                variant_index=1,
                duration_seconds=10,
                garment_material=material,
                site_profile=get_site_style_profile("pagewings"),
                shot_action_contexts=(ActionContext(), ActionContext(), ActionContext()),
                shot_evidence_sources=("unknown", "unknown", "unknown"),
            )
        )

    sweater_prompt = compile_ootd("short_mohair").master_prompt
    assert "衣服是短马海毛材质。" in sweater_prompt
    assert "领口、袖口、下摆和有无罗纹严格以商品参考图为准" in sweater_prompt
    assert "不得自动增删罗纹或加长绒毛" in sweater_prompt
    assert "罗纹严格以商品参考图为准" in sweater_prompt

    for material in ("mesh_jersey", "cotton", "unknown"):
        prompt = compile_ootd(material).master_prompt
        assert "短马海毛" not in prompt
        assert "短马海毛" not in prompt
        assert "2. 模特展示衣服细节" in prompt


@pytest.mark.parametrize(
    ("confidence", "material", "evidence", "expected"),
    [
        ("high", "short_mohair", "Dense short visible pile", "short_mohair"),
        ("high", "mesh_jersey", "Repeated open holes visible", "mesh_jersey"),
        ("uncertain", "mesh_jersey", "Possible holes", "unknown"),
        ("high", "unknown", "Not enough evidence", "unknown"),
        ("high", "short_mohair", "", "unknown"),
    ],
)
def test_visual_material_evidence_requires_confidence_and_evidence(
    confidence, material, evidence, expected
):
    facts = ImageMotionFacts(
        asset_id="one",
        observations=[],
        material_observation=MaterialObservation(
            material=material,
            confidence=confidence,
            evidence=evidence,
        ),
    )

    assert facts.garment_material() == expected


def test_conflicting_visual_materials_fall_back_to_explicit_task_text():
    observations = [
        ObservedMotion(garment_material="short_mohair"),
        ObservedMotion(garment_material="mesh_jersey"),
    ]
    by_id = {
        uuid.uuid4(): SimpleNamespace(original_filename="storyboard.png"),
        uuid.uuid4(): SimpleNamespace(original_filename="product.png"),
    }
    ordered_inputs = [SimpleNamespace(upload_id=next(iter(by_id)))]

    material, source = _resolve_garment_material(
        observations,
        title="网眼球衣新品",
        creative_brief="",
        ordered_inputs=ordered_inputs,
        ordered_subject_inputs=[],
        by_id=by_id,
    )

    assert material == "mesh_jersey"
    assert source == "task_text"


@pytest.mark.parametrize(
    ("category", "required"),
    [
        ("短马海毛毛衣", "衣服是短马海毛材质。"),
        ("网眼球衣", "网眼、轻薄感与垂坠自然"),
    ],
)
def test_legacy_analysis_video_prompt_also_includes_material(category, required):
    shot = TimelineShot(
        start=0,
        end=10,
        reference_asset_ids=("asset-1",),
        entry_state="正面站定",
        action="自然展示",
        exit_state="稳定停留",
        camera="平稳镜头",
        product_display="服装材质和印花清晰",
        shot_function="商品展示",
    )

    prompt = render_prompt(
        VariantKind.STABLE,
        [shot],
        ["IMG-1"],
        "",
        garment_category=category,
    )

    assert required in prompt
    assert len(prompt) <= 1600


def test_default_custom_video_prompt_no_longer_forces_every_product_to_be_jersey():
    content = next(
        content for scope, _, content in DEFAULT_PROMPT_PRESET_SPECS if scope == "video"
    )

    assert content.startswith("生成一段动态视频，智能分镜。")
    assert "动作包含：" in content
    assert garment_material_guidance("unknown") in content
    assert "1. 模特做一个自然的展示动作" in content
    assert "2. 模特展示衣服细节" in content
    assert "3. 模特再做一个自然的收尾展示动作" in content
    assert "iPhone17" in content
    assert "8K" in content
    assert "短马海毛" not in content
    assert "100%还原球衣" not in content


def test_short_mohair_video_uses_automatic_modules_instead_of_copied_copy():
    presets = {
        (scope, name): content
        for scope, name, content in DEFAULT_PROMPT_PRESET_SPECS
    }

    assert presets[("image_fusion", "短马海毛")] == SHORT_MOHAIR_IMAGE_PROMPT
    assert SHORT_MOHAIR_IMAGE_PROMPT.startswith("图1模特穿上图2的短马海毛毛衣")
    assert "图2衣服图案版型100%还原" in SHORT_MOHAIR_IMAGE_PROMPT
    video_prompt = presets[("video", "默认 1")]
    assert "男生" not in video_prompt
    assert "短马海毛毛衣" not in video_prompt
    assert "UGC Lifestyle Scene" not in video_prompt
    assert "没有螺纹，没有螺纹" not in video_prompt


@pytest.mark.parametrize("mode", ["fusion", "storyboard"])
def test_image_generation_prompt_includes_short_mohair_structure(mode):
    manifest = ImageBatchManifest.model_validate(
        {
            "clientBatchId": f"sweater-{mode}",
            "name": "sweater",
            "provider": "nanobanana",
            "modes": [mode],
            "resultCount": 1,
            "paidSubmissionConsent": True,
            "expectedGenerationCount": 1,
            "items": [
                {
                    "clientTaskId": "sweater-item",
                    "outfitId": "sweater-1",
                    "outfitName": "短马海毛毛衣",
                    "prompts": {
                        "fusion": "将图二服装自然穿在图一人物身上",
                        "storyboard": "生成一张自然展示图二服装的人物照片",
                    },
                    "referenceFileIndexes": [0, 1],
                    "referenceRoles": [
                        {"fileIndex": 0, "role": "model.front"},
                        {"fileIndex": 1, "role": "product.front"},
                    ],
                }
            ],
        }
    )

    spec = _expand_manifest(manifest, file_count=2)[0]

    assert "衣服是短马海毛材质。" in spec.prompt
    assert "领口、袖口、下摆和有无罗纹严格以商品参考图为准" in spec.prompt
    assert "不得自动增删罗纹或加长绒毛" in spec.prompt
