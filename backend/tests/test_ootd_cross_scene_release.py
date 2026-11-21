from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ootd_cross_scene import (
    CROSS_SCENE_PROFILE, cross_scene_director_prompt, cross_scene_enabled,
    product_analysis_enabled,
)
from libtv_agent.services.ootd_simple import PROFILE, SYSTEM_PROMPT, plan_simple_ootd
from libtv_agent.services.ootd_dynamic import DYNAMIC_OOTD_PROFILE


def test_cross_scene_is_default_and_baseline_is_unchanged(monkeypatch):
    monkeypatch.delenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", raising=False)
    assert cross_scene_enabled()
    new = cross_scene_director_prompt(SYSTEM_PROMPT)
    assert PROFILE == DYNAMIC_OOTD_PROFILE
    assert "PRODUCT-AWARE CROSS-SCENE DIRECTOR" in new
    assert "FIRST shot starts" in new
    assert "DIRECT CUT may establish a new believable everyday" in new
    assert "garment graphics" in new
    assert "No invented product claims" in new
    assert "the exact supplied first-frame setting" in new
    assert "No invented product claims, pricing, promotional promises, props, people" not in new
    assert "scene, light and original props" not in new
    assert "No JSON" in new
    assert "warp lettering and artwork" in new
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


def test_cross_scene_can_be_rolled_back_without_source_change(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "0")
    assert not cross_scene_enabled()
    assert cross_scene_director_prompt(SYSTEM_PROMPT) == SYSTEM_PROMPT
    assert not product_analysis_enabled(video_type="ootd", product_count=1)
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "true")
    assert product_analysis_enabled(video_type="ootd", product_count=1)
    assert not product_analysis_enabled(video_type="ootd", product_count=0)
    assert not product_analysis_enabled(video_type="unboxing", product_count=1)


@pytest.mark.asyncio
async def test_default_crossscene_uses_images_for_product_aware_planning(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "0")
    calls = []
    class Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text=(
                "10秒竖屏，镜头1从输入首帧书店实景开始，人物看到书友活动时自然抬头，"
                "真实日常UGC，展示商品正面完整文字。直接切镜到可信的户外二手书市，"
                "她正在挑选旧书，发现一本熟悉的书自然微笑与同伴交流。直接切镜到"
                "正后方近景，保持相同穿着并完整清晰呈现原图证实的背面图案，"
                "不拉扯衣料，不改变图案，无旁白无字幕。"
            ))
    items=[AnalysisImageInput(asset_id="first",order=1,role="STORYBOARD",
            image_url="data:image/jpeg;base64,AAAA")]
    products=[AnalysisImageInput(asset_id="original-product",order=1,role="PRODUCT",
            image_url="data:image/jpeg;base64,BBBB")]
    results=await plan_simple_ootd(settings=Settings(app_env="test",_env_file=None),
        storyboard_images=items,product_images=products,analyze_product_images=True,
        creative_brief="展示这件双面印花T恤",duration_seconds=10,
        client=SimpleNamespace(responses=Responses()))
    assert len(results)==1
    sent=calls[0]["input"]
    assert "PRODUCT-AWARE CROSS-SCENE DIRECTOR" in sent[0]["content"][0]["text"]
    images=[part for part in sent[1]["content"] if part["type"]=="input_image"]
    assert len(images)==2
    assert images[0]["image_url"].endswith("AAAA")
    assert images[1]["image_url"].endswith("BBBB")
    assert results[0].prompt.startswith("10秒")
