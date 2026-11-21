from __future__ import annotations
from types import SimpleNamespace
import pytest

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ootd_simple import plan_simple_ootd

class MockResponses:
    def __init__(self, output: str):
        self.output = output
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output)
@pytest.mark.asyncio
async def test_unboxing_uses_independent_director_prompt_and_does_not_invent_packaging():
    from libtv_agent.services.ootd_simple import UNBOXING_PROFILE, UNBOXING_SYSTEM_PROMPT
    prompt = (
        "10秒竖屏手机实拍，从现有首帧商品与包装自然起镜，手部动作真实。"
        "0-3秒轻轻打开已有包装，直接切镜至商品取出，3-7秒正常速度展开，"
        "再次直接切镜展示商品原始印花文字与面料，7-10秒回到商品整体。"
        "不新增盒子、道具或商品文字，无字幕无音乐。"
    )
    responses = MockResponses(prompt)
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="first", order=1, role="STORYBOARD", image_url="fixture:unboxing-frame",
        )],
        product_images=[], creative_brief="保持首帧已有包装；不要添加不存在的配件。",
        duration_seconds=10, video_type="unboxing", client=SimpleNamespace(responses=responses),
    )
    assert result[0].planner_version == UNBOXING_PROFILE
    assert result[0].prompt == prompt
    system_text = responses.calls[0]["input"][0]["content"][0]["text"]
    user_text = responses.calls[0]["input"][1]["content"][0]["text"]
    assert system_text == UNBOXING_SYSTEM_PROMPT
    assert "never invent packaging" in system_text
    assert "unboxing/product-reveal" in user_text
    assert "不要添加不存在的配件" in user_text




def test_video_task_schema_preserves_legacy_ootd_hash_and_carries_unboxing():
    import uuid
    from libtv_agent.domain.schemas import VideoTaskCreate

    payload = {
        "clientTaskId": "type-check", "title": "Test clip",
        "storyboards": [{"uploadId": str(uuid.uuid4()), "order": 1}],
        "siteProfileId": "pagewings", "paidSubmissionConsent": True,
    }
    default = VideoTaskCreate.model_validate(payload)
    assert default.video_type == "ootd"
    assert "videoType" not in default.model_dump(mode="json", by_alias=True)
    selected = VideoTaskCreate.model_validate({**payload, "videoType": "unboxing"})
    assert selected.video_type == "unboxing"
    assert selected.model_dump(mode="json", by_alias=True)["videoType"] == "unboxing"


def test_video_type_defaults_to_ootd_for_legacy_cut_tasks():
    from libtv_agent.services.cutflow_video_type import normalize_video_type, task_video_type
    assert normalize_video_type("unboxing") == "unboxing"
    assert all(normalize_video_type(value) == "ootd" for value in (None, "", "fashion_product_video", "incorrect", 10))
    assert task_video_type(SimpleNamespace(settings_json=None)) == "ootd"
    assert task_video_type(SimpleNamespace(settings_json={})) == "ootd"
    assert task_video_type(SimpleNamespace(settings_json={"videoType": "unboxing"})) == "unboxing"
    assert task_video_type(SimpleNamespace(settings_json={"videoType": "bad"})) == "ootd"
