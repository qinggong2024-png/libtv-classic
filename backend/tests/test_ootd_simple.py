from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import CreativePlannerError
from libtv_agent.services.ootd_cross_scene import cross_scene_director_prompt, default_cross_scene_brief
from libtv_agent.services.ootd_direction import FOCUS_LABELS
from libtv_agent.services.ootd_dynamic import dynamic_ootd_prompt, dynamic_ootd_brief
from libtv_agent.services.ootd_simple import (
    KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
    MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
    MULTI_REFERENCE_SYSTEM_PROMPT,
    PROFILE,
    SYSTEM_PROMPT,
    eligible,
    plan_simple_ootd,
)


def test_natural_language_baseline_preserves_print_without_a_motion_library():
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == (
        "c0e97bb7ad23879ca5ef7a8e3c64fa8a21b86678fe51eb4cdd174adfa264d5b5"
    )
    for system_prompt in (
        SYSTEM_PROMPT,
        KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
        MULTI_REFERENCE_SYSTEM_PROMPT,
        MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
    ):
        assert "direct cut" in system_prompt.lower()
        assert "independently choose" in system_prompt
        assert "designated display moment" not in system_prompt
        assert "Choose one plausible everyday activity" not in system_prompt
        assert "generic hem-pull, sleeve-touch" not in system_prompt
        assert "front-facing or shallow-angle" not in system_prompt
        assert "Each shot should have one natural action" in system_prompt
        assert "Never grip, stretch, tug sideways or downward" in system_prompt
        assert "warp lettering and artwork" in system_prompt
        assert "lively purposeful action" in system_prompt
        assert "FRONT artwork and fabric closeups" in system_prompt
        assert "BACK views only when" in system_prompt
        assert "Never stage standalone side-profile" in system_prompt
        assert "With front-only references" in system_prompt
        assert "use a DIRECT CUT to a genuinely referenced back shot" in system_prompt
        assert "use natural, small" not in system_prompt
        assert "low-risk" not in system_prompt.lower()
        assert "No JSON" in system_prompt


class MockResponses:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs[len(self.calls) - 1])


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", range(5, 16))
@pytest.mark.parametrize("storyboard_count,analyze_products,intensity", [
    (1, False, "low"), (1, True, "standard"),
    (2, False, "high"), (2, True, "standard"),
])
async def test_behavior_guidance_reaches_each_image_route_without_enforcing_actions(
    storyboard_count, analyze_products, duration, intensity,
):
    # A deliberately repetitive response must still pass the existing contract:
    # this revision directs Sol, it does not reject or rewrite its action choices.
    prompt = ("从首帧站姿开始，人物看向树叶再看回镜头微笑。直接切镜，"
              "保持原有衣服、印花、文字、人物和环境，再看向树叶回头微笑。"
              "正常速度、真实手持，无字幕无音乐，衣服自然垂落，人物表情放松。")
    responses = MockResponses([prompt])
    brief = "保持坐姿，不走动，不转身，只做轻柔手臂动作。"
    results = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id=f"s{i}", order=i, role="STORYBOARD", image_url=f"fixture:shot{i}",
        ) for i in range(1, storyboard_count + 1)],
        product_images=[AnalysisImageInput(
            asset_id="p", order=1, role="PRODUCT", image_url="fixture:product",
        )],
        analyze_product_images=analyze_products, creative_brief=brief,
        action_intensity=intensity, duration_seconds=duration,
        client=SimpleNamespace(responses=responses),
    )
    assert len(responses.calls) == 1
    system, user = responses.calls[0]["input"]
    expected_system = {
        (False, False): KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
        (False, True): SYSTEM_PROMPT,
        (True, False): MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
        (True, True): MULTI_REFERENCE_SYSTEM_PROMPT,
    }[(storyboard_count > 1, analyze_products)]
    assert system["content"][0]["text"] == dynamic_ootd_prompt(
        duration_seconds=duration, storyboard_count=storyboard_count,
        product_visible_to_sol=analyze_products,
    )
    assert f"{duration}-second" in user["content"][0]["text"]
    assert eligible(generation_mode="automatic", cut_mode="explicit_cuts",
                    provider="cli", model="kling-video-v3_0_omni",
                    ai_model="gpt-6.1-sol", duration=duration)
    assert eligible(generation_mode="automatic", cut_mode="explicit_cuts",
                    provider="api", model="kling-3.0-omni",
                    ai_model="gpt-6.1-sol", duration=duration)
    assert brief in user["content"][0]["text"]
    assert "highest creative priority" in expected_system
    images = [part for part in user["content"] if part["type"] == "input_image"]
    assert len(images) == storyboard_count + int(analyze_products)
    if intensity == "standard":
        assert not any("Creative preferences" in part.get("text", "")
                       or "complete physical actions" in part.get("text", "")
                       for part in user["content"])
    assert results[0].prompt == prompt
    assert results[0].beats == ()
    assert results[0].planner_version == PROFILE


@pytest.mark.asyncio
async def test_sol_ootd_uses_first_frame_user_brief_and_original_text():
    prompt = (
        "10秒竖版9:16，镜头一0-3.2秒：从首帧半身构图起镜，自然整理开衫。"
        "直接切镜。镜头二3.2-6.4秒：衣服细节特写，展示原始花纹。"
        "直接切镜。镜头三6.4-10秒：自然侧身，展示版型。无音频，不加字幕。"
    )
    responses = MockResponses([prompt])
    settings = Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None)
    results = await plan_simple_ootd(
        settings=settings,
        storyboard_images=[AnalysisImageInput(
            asset_id="first", order=1, role="STORYBOARD",
            image_url="data:image/jpeg;base64,AAAA",
        )],
        product_images=[AnalysisImageInput(
            asset_id="product", order=1, role="PRODUCT",
            image_url="data:image/jpeg;base64,BBBB",
        )],
        creative_brief="不要转身，重点展示图案。",
        duration_seconds=10,
        client=SimpleNamespace(responses=responses),
    )
    assert len(results) == 1
    assert results[0].prompt == prompt
    assert results[0].planner_version == PROFILE
    assert results[0].facts[0]["person_count_verified"] is False
    call = responses.calls[0]
    assert call["model"] == "gpt-6.1-sol"
    user = call["input"][1]["content"]
    assert "不要转身，重点展示图案。" in user[0]["text"]
    assert "ONLY starting frame" in user[1]["text"]
    assert user[2]["image_url"].endswith("AAAA")
    assert user[2]["detail"] == "high"
    assert len([part for part in user if part["type"] == "input_image"]) == 1
    assert "@image_2" in user[3]["text"]
    assert "NOT supplied to you" in user[3]["text"]


@pytest.mark.asyncio
async def test_no_cut_rejected_before_paid_provider():
    responses = MockResponses([
        "10秒手机视频，镜头一直对着人物展示衣服，始终同一镜头，动作自然。" * 3,
    ])
    with pytest.raises(CreativePlannerError) as error:
        await plan_simple_ootd(
            settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
            storyboard_images=[AnalysisImageInput(
                asset_id="first", order=1, role="STORYBOARD",
                image_url="data:image/jpeg;base64,AAAA",
            )],
            product_images=[], creative_brief="", duration_seconds=10,
            client=SimpleNamespace(responses=responses),
        )
    assert error.value.code == "OOTD_PROMPT_INVALID"
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_multiple_storyboards_are_ordered_and_product_reference_is_distinct():
    prompt = ("首帧半身开始正常速度整理衣服，直接切镜至第二张分镜对应的近景，"
              "自然展示衣服印花和衣领，回到自然半身展示，全程保留原有模特、"
              "现场环境、服装版型和商品文字，不慢动作、不新增字幕或道具。")
    responses = MockResponses([prompt])
    await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[
            AnalysisImageInput(asset_id=str(i), order=i, role="STORYBOARD",
                               image_url=f"fixture:shot-{i}")
            for i in (1, 2)
        ],
        product_images=[AnalysisImageInput(
            asset_id="product", order=1, role="PRODUCT", image_url="fixture:product",
        )],
        creative_brief="", duration_seconds=10,
        client=SimpleNamespace(responses=responses),
    )
    user = responses.calls[0]["input"][1]["content"]
    assert "ONLY starting frame" in user[1]["text"]
    assert "additional ordered storyboard shot reference" in user[3]["text"]
    assert "@image_3" in user[5]["text"]
    assert [user[i]["image_url"] for i in (2, 4)] == [
        "fixture:shot-1", "fixture:shot-2",
    ]
    assert [user[i]["detail"] for i in (2, 4)] == ["high", "low"]


@pytest.mark.asyncio
async def test_sol_two_storyboards_two_products_have_explicit_numbered_roles():
    prompt = (
        "10秒9:16竖屏手机实拍，图片1人物自然整理衣袖，镜头果断切镜到"
        "图片2构图下的衣服特写，展示与图片3和图片4一致的原有商品印花、"
        "细节与版型，正常速度自然衔接，保持原场景人物，不加字幕和背景音乐。"
    )
    responses = MockResponses([prompt])
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[
            AnalysisImageInput(asset_id=f"shot{i}", order=i, role="STORYBOARD",
                               image_url=f"fixture:shot{i}")
            for i in (1, 2)
        ],
        product_images=[
            AnalysisImageInput(asset_id=f"product{i}", order=i, role="PRODUCT",
                               image_url=f"fixture:product{i}")
            for i in (1, 2)
        ],
        analyze_product_images=True,
        creative_brief="清晰展示产品文字", duration_seconds=10,
        client=SimpleNamespace(responses=responses),
    )
    assert result[0].prompt == prompt
    call = responses.calls[0]["input"]
    assert "additional storyboard images are ordered optional" in call[0]["content"][0]["text"]
    user = call[1]["content"]
    assert "清晰展示产品文字" in user[0]["text"]
    assert "ONLY starting frame" in user[1]["text"]
    assert "additional ordered storyboard" in user[3]["text"]
    assert "primary product design reference" in user[5]["text"]
    assert "supplementary product detail reference" in user[7]["text"]
    assert [user[i]["image_url"] for i in (2, 4, 6, 8)] == [
        "fixture:shot1", "fixture:shot2", "fixture:product1", "fixture:product2",
    ]
    assert [user[i]["detail"] for i in (2, 4, 6, 8)] == ["high", "low", "high", "high"]


@pytest.mark.asyncio
@pytest.mark.parametrize("focus,with_product,expected_images", [
    ("overall", True, 1), ("fit", True, 1), ("print", True, 2),
    ("material", True, 2), ("back", True, 2), ("back", False, 1),
])
async def test_focus_uses_available_product_evidence_without_adding_a_gate(
    focus, with_product, expected_images,
):
    prompt = "保持原有商品和人物，自然展示穿搭，直接切镜后展示可见细节，不添加字幕。" * 3
    responses = MockResponses([prompt])
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="s", order=1, role="STORYBOARD", image_url="fixture:first",
        )],
        product_images=[AnalysisImageInput(
            asset_id="p", order=1, role="PRODUCT", image_url="fixture:product",
        )] if with_product else [],
        display_focus=focus, analyze_product_images=False,
        creative_brief="不要转身", brand_tone="沉静自然", action_intensity="high",
        duration_seconds=10, client=SimpleNamespace(responses=responses),
    )
    user = responses.calls[0]["input"][1]["content"]
    images = [item for item in user if item["type"] == "input_image"]
    assert len(images) == expected_images
    assert all(item["detail"] == "high" for item in images)
    text = " ".join(item.get("text", "") for item in user)
    assert "沉静自然" not in text and "Expressive" in text and "不要转身" in text
    assert "visible reference evidence" in text
    if focus != "overall":
        assert FOCUS_LABELS[focus] in text
    assert result[0].prompt == prompt  # No rewriting or new output checker.
    assert len(responses.calls) == 1


@pytest.mark.asyncio
async def test_default_request_has_no_added_creative_preferences():
    prompt = "从原首帧开始自然展示服装，直接切镜后展示细节，保持人物商品一致，不添加字幕。" * 3
    responses = MockResponses([prompt])
    await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="s", order=1, role="STORYBOARD", image_url="fixture:first",
        )], product_images=[AnalysisImageInput(
            asset_id="p", order=1, role="PRODUCT", image_url="fixture:product",
        )], analyze_product_images=True, creative_brief="", duration_seconds=10,
        brand_tone="日常、俏皮、复古，图形清楚",
        client=SimpleNamespace(responses=responses),
    )
    system, user = responses.calls[0]["input"]
    assert system["content"][0]["text"] == dynamic_ootd_prompt(
        duration_seconds=10, storyboard_count=1, product_visible_to_sol=True,
    )
    assert len(user["content"]) == 5  # duration + two image labels and images
    assert user["content"][0]["text"].endswith(dynamic_ootd_brief())
    assert user["content"][1]["text"] == "Image 1: the ONLY starting frame"
    assert user["content"][3]["text"] == "Image 2: product design reference, not a starting frame"


@pytest.mark.asyncio
async def test_variants_use_previous_output_without_fixed_directions():
    prompt = "从首帧开始正常速度展示衣服，直接切镜后展示原有印花，保持人物与商品一致。" * 3
    responses = MockResponses([prompt] * 4)
    results = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="s", order=1, role="STORYBOARD", image_url="fixture:first",
        )], product_images=[], display_focus="print", creative_brief="不要转身",
        duration_seconds=10, variant_count=4, client=SimpleNamespace(responses=responses),
    )
    for index, call in enumerate(responses.calls):
        texts = [item.get("text", "") for item in call["input"][1]["content"]]
        assert not any(text.startswith("Execution ") for text in texts)
        assert any("印花与文字" in text for text in texts)
        assert any("Earlier variants:" in text for text in texts) == (index > 0)
    assert len(responses.calls) == 4
    assert len({result.creative_idea for result in results}) == 4
    assert all(result.prompt == prompt for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 8])
async def test_invalid_storyboard_count_fails_before_sol(count):
    with pytest.raises(CreativePlannerError) as error:
        await plan_simple_ootd(
            settings=Settings(app_env="test", _env_file=None),
            storyboard_images=[
                AnalysisImageInput(asset_id=str(i), order=i, role="STORYBOARD", image_url="a")
                for i in range(1, count + 1)
            ],
            product_images=[], creative_brief="", duration_seconds=10,
            client=SimpleNamespace(responses=MockResponses([])),
        )
    assert error.value.code == "OOTD_STORYBOARD_COUNT"


def test_ootd_eligibility_only_supported_cli_sol():
    base = dict(generation_mode="automatic", cut_mode="explicit_cuts",
                provider="cli", model="kling-video-v3_0_omni",
                ai_model="gpt-6.1-sol", duration=10)
    assert eligible(**base)
    assert eligible(**{**base, "duration": 5})
    assert eligible(**{**base, "duration": 15})
    assert not eligible(**{**base, "provider": "api"})
    assert not eligible(**{**base, "generation_mode": "custom"})
    assert not eligible(**{**base, "ai_model": "gpt-5.6-luna"})


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,shots", [(5, 1), (10, 2), (15, 4)])
async def test_sol_preserves_variable_shot_count_and_requested_duration(duration, shots):
    # Replayed text deliberately varies shot count. The planner may not force three
    # shots, preselected cut timestamps, or a legacy JSON/shot compiler onto it.
    prompt = "直接切镜。".join(
        f"镜头{i + 1}：保持首帧中的原有商品与人物，自然整理衣袖，真实手持镜头，"
        "保持原有印花、颜色和光线，动作速度自然，无字幕无音频。"
        for i in range(shots)
    )
    if len(prompt) < 80:
        prompt += "保持真实日常展示和原始服装纹理，不添加人物、道具、商品卖点或文字。"
    responses = MockResponses([prompt])
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="first", order=1, role="STORYBOARD", image_url="fixture:first",
        )], product_images=[], creative_brief="不转身", duration_seconds=duration,
        client=SimpleNamespace(responses=responses),
    )
    assert result[0].prompt == prompt
    assert result[0].beats == ()
    assert len(responses.calls) == 1
    assert f"{duration}-second" in responses.calls[0]["input"][1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_sol_provider_failure_is_not_retried_or_sent_to_fallback_model():
    create = AsyncMock(side_effect=TimeoutError("offline timeout"))
    with pytest.raises(CreativePlannerError) as caught:
        await plan_simple_ootd(
            settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
            storyboard_images=[AnalysisImageInput(
                asset_id="first", order=1, role="STORYBOARD", image_url="fixture:first",
            )], product_images=[], creative_brief="", duration_seconds=10,
            client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        )
    assert caught.value.code == "AI_CREATIVE_REQUEST_TIMEOUT"
    assert caught.value.request_attempts == 1
    create.assert_awaited_once()
    assert create.call_args.kwargs["model"] == "gpt-6.1-sol"


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,variants", [(4, 1), (16, 1), (10, 0), (10, 5)])
async def test_unsupported_ootd_options_fail_before_model(duration, variants):
    create = AsyncMock(side_effect=AssertionError("model must not be called"))
    with pytest.raises(CreativePlannerError) as caught:
        await plan_simple_ootd(
            settings=Settings(app_env="test", _env_file=None),
            storyboard_images=[AnalysisImageInput(
                asset_id="first", order=1, role="STORYBOARD", image_url="fixture:first",
            )], product_images=[], creative_brief="", duration_seconds=duration,
            variant_count=variants,
            client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        )
    assert caught.value.code == "OOTD_UNSUPPORTED_OPTIONS"
    assert caught.value.request_attempts == 0
    create.assert_not_called()
