"""Director mode routing must not mix cross-scene and reference-guided prompts."""
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services.ootd_simple import (
    SYSTEM_PROMPT, plan_simple_ootd,
)


class Responses:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=(
            "十秒手机竖屏。镜头一0-3秒：人物在原位置自然看向镜头，展示胸前印花。"
            "直接切镜。镜头二3-7秒：镜头靠近原始图案，衣服自然垂落。"
            "直接切镜。镜头三7-10秒：人物放松站立，镜头回到中景。"
            "真实手持，无字幕、无音乐，服装图案和人物保持一致。"
        ))


@pytest.mark.asyncio
async def test_reference_mode_uses_untouched_baseline_and_original_default_brief(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    responses = Responses()
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="first", order=1, role="STORYBOARD", image_url="fixture:shot",
        )],
        product_images=[],
        creative_brief="",
        director_mode="reference_guided",
        duration_seconds=10,
        client=SimpleNamespace(responses=responses),
    )
    system, user = responses.calls[0]["input"]
    assert system["content"][0]["text"] == SYSTEM_PROMPT
    assert user["content"][0]["text"].endswith("Natural outfit display with clear cuts.")
    assert result[0].planner_version == "allwe-ootd-sol-simple-v1"


def test_request_defaults_to_cross_scene_and_validates_reference_choice():
    assert VideoTaskCreate.model_fields["director_mode"].default == "cross_scene_v2"
    assert "reference_guided" in VideoTaskCreate.model_fields["director_mode"].annotation.__args__
