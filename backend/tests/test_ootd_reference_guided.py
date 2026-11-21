from __future__ import annotations

from types import SimpleNamespace

import pytest

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import CreativePlannerError
from libtv_agent.services.ootd_reference_guided import (
    reference_guided_system_prompt,
    reference_guided_violation,
)
from libtv_agent.services.ootd_simple import (
    MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT,
    MULTI_REFERENCE_SYSTEM_PROMPT,
    plan_simple_ootd,
)


GOOD_PROMPT = (
    "10秒手机视频，@image_1 从原图室内书架旁起镜，保留书和帆布包；"
    "0到4秒，人物自然看向镜头，印花清晰。"
    "4秒直接切镜，@image_2 在原图室外花园中背对镜头，沿用室外背景、"
    "散发、光线和书包，背面的原始图案完整清晰，动作自然；"
    "10秒结束，无字幕无音乐，人物与原商品设计保持一致。"
)
BAD_PROMPT = (
    "10秒竖屏原图起镜，室内书架前自然整理衣服，直接切镜；"
    "@image_2 仅参考后段背面取景，不照搬室外场景、发型或姿势。"
    "后段继续在室内展示服装，人物自然转身，原商品文字完整。"
)
BAD_SKIP = (
    "10秒手机视频，原首帧站立，人物自然动作，直接切镜。"
    "@image_2 为可选后续背面分镜参考，本片不采用。"
    "继续在首帧室内拍摄正面印花与领口，构图稳定，正常速度，无字幕。"
)


class MockResponses:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs[len(self.calls) - 1])


def _storyboards():
    return [
        AnalysisImageInput(
            asset_id=f"shot{i}", order=i, role="STORYBOARD",
            image_url=f"fixture:shot{i}",
        )
        for i in (1, 2)
    ]


@pytest.mark.parametrize("bad", [BAD_PROMPT, BAD_SKIP])
def test_rejects_known_pw_20261121_downgrades(bad):
    assert reference_guided_violation(bad, storyboard_count=2)


def test_allows_referenced_later_scene_and_explicit_user_exception():
    assert reference_guided_violation(GOOD_PROMPT, storyboard_count=2) is None
    # This wording alone does not prove that the later reference was ignored.
    assert reference_guided_violation(
        GOOD_PROMPT.replace("@image_2 在原图室外花园中背对镜头，",
                            "@image_2 仅作后段背面分镜参考，在原图室外花园中背对镜头，"),
        storyboard_count=2,
    ) is None
    assert reference_guided_violation(
        BAD_SKIP, storyboard_count=2,
        creative_brief="用户明确指定图二为可选分镜，本片不采用。",
    ) is None


def test_reference_system_prompt_is_specific_to_later_visual_anchors():
    for base in (MULTI_REFERENCE_SYSTEM_PROMPT, MULTI_KLING_ONLY_PRODUCT_SYSTEM_PROMPT):
        result = reference_guided_system_prompt(base)
        assert "REQUIRED ordered visual anchors" in result
        assert "actual referenced location" in result
        assert "every shot to reuse the opening location" in result
        assert "optional later shots" not in result
        assert ("Product images" in result or "Any separately supplied product images" in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("analyze_product_images", [True, False])
async def test_reference_guided_uses_high_detail_and_repairs_downgrade(
    analyze_product_images,
):
    responses = MockResponses([BAD_PROMPT, GOOD_PROMPT])
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.2-sol", _env_file=None),
        storyboard_images=_storyboards(),
        product_images=[AnalysisImageInput(
            asset_id="product", order=1, role="PRODUCT", image_url="fixture:product",
        )],
        analyze_product_images=analyze_product_images,
        creative_brief="自然转身", duration_seconds=10,
        director_mode="reference_guided",
        client=SimpleNamespace(responses=responses),
    )
    assert result[0].prompt == GOOD_PROMPT
    assert result[0].request_attempts == 2
    first = responses.calls[0]["input"]
    assert "REQUIRED ordered visual anchors" in first[0]["content"][0]["text"]
    image_parts = [item for item in first[1]["content"] if item["type"] == "input_image"]
    assert [x["detail"] for x in image_parts[:2]] == ["high", "high"]
    assert "required ordered later-shot visual reference" in first[1]["content"][3]["text"]
    repair_user = responses.calls[1]["input"][1]["content"]
    assert any("previous draft downgraded" in str(x.get("text", "")).lower()
               for x in repair_user)


@pytest.mark.asyncio
async def test_reference_guided_refuses_to_pass_unrepaired_draft():
    responses = MockResponses([BAD_SKIP, BAD_SKIP])
    with pytest.raises(CreativePlannerError) as exc:
        await plan_simple_ootd(
            settings=Settings(app_env="test", _env_file=None),
            storyboard_images=_storyboards(),
            product_images=[], creative_brief="", duration_seconds=10,
            director_mode="reference_guided",
            client=SimpleNamespace(responses=responses),
        )
    assert exc.value.code == "OOTD_REFERENCE_GUIDED_INVALID"
    assert exc.value.request_attempts == 2
    assert len(responses.calls) == 2


@pytest.mark.asyncio
async def test_cross_scene_v2_has_not_changed_its_image_detail_and_flow():
    responses = MockResponses([BAD_SKIP])
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=_storyboards(), product_images=[],
        creative_brief="", duration_seconds=10,
        director_mode="cross_scene_v2",
        client=SimpleNamespace(responses=responses),
    )
    assert result[0].prompt == BAD_SKIP  # V2 deliberately retains autonomous choice
    assert len(responses.calls) == 1
    parts = responses.calls[0]["input"][1]["content"]
    assert [x["detail"] for x in parts if x["type"] == "input_image"] == [
        "high", "low",
    ]
