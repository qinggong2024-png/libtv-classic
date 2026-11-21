"""Dynamic OOTD default and legacy rollback contract tests; zero provider charges."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ootd_cross_scene import cross_scene_director_prompt, default_cross_scene_brief
from libtv_agent.services.ootd_dynamic import (
    DYNAMIC_OOTD_PROFILE, dynamic_ootd_brief, dynamic_ootd_enabled,
    dynamic_ootd_prompt,
)
from libtv_agent.services.ootd_simple import SYSTEM_PROMPT, plan_simple_ootd


class FakeResponses:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=(
            "10秒9:16美式手机随拍，从首帧开始自然展示整体穿搭，保持人物和服装原样。"
            "0–3秒展示整体；直接切镜到正面中景，3–7秒展示完整印花。"
            "直接切镜到全身角度，7–10秒人物自然移动，衣料轻轻摆动。"
            "全程不更改印花、字样和穿搭，不加字幕或音乐。"
        ))


def images():
    return [
        AnalysisImageInput(asset_id="first", order=1, role="STORYBOARD", image_url="fixture:scene"),
    ], [
        AnalysisImageInput(asset_id="product", order=1, role="PRODUCT", image_url="fixture:original"),
    ]


def test_frozen_paid_verified_system_and_brief_hashes():
    assert hashlib.sha256(dynamic_ootd_prompt(
        duration_seconds=10, storyboard_count=1, product_visible_to_sol=True,
    ).encode()).hexdigest() == "ef2d1d13041fb8b580dbc845d6519a084f2fe207f4c21645b429f89df3aba0d1"
    assert hashlib.sha256(dynamic_ootd_brief().encode()).hexdigest() == (
        "2b0e4c7a82b9a3628a388daf9288b8f5ac0a776df1f0abe517a4b573df925984"
    )
    assert "not a mini-story" in dynamic_ootd_brief()


@pytest.mark.asyncio
async def test_dynamic_default_routes_both_system_and_user_brief(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "1")
    assert dynamic_ootd_enabled()
    response = FakeResponses()
    story, product = images()
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=story, product_images=product, analyze_product_images=True,
        creative_brief="", duration_seconds=10,
        client=SimpleNamespace(responses=response),
    )
    system, user = response.calls[0]["input"]
    assert system["content"][0]["text"] == dynamic_ootd_prompt(
        duration_seconds=10, storyboard_count=1, product_visible_to_sol=True,
    )
    assert user["content"][0]["text"].endswith(dynamic_ootd_brief())
    assert len([p for p in user["content"] if p["type"] == "input_image"]) == 2
    assert result[0].planner_version == DYNAMIC_OOTD_PROFILE


@pytest.mark.asyncio
async def test_disable_dynamic_restores_original_legacy_b(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "0")
    response = FakeResponses()
    story, product = images()
    await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=story, product_images=product, analyze_product_images=True,
        creative_brief="", duration_seconds=10,
        client=SimpleNamespace(responses=response),
    )
    system, user = response.calls[0]["input"]
    assert system["content"][0]["text"] == cross_scene_director_prompt(SYSTEM_PROMPT)
    assert user["content"][0]["text"].endswith(default_cross_scene_brief())


@pytest.mark.asyncio
async def test_reference_guided_keeps_its_own_baseline(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "1")
    response = FakeResponses()
    story, product = images()
    result = await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=story, product_images=product,
        creative_brief="", duration_seconds=10,
        director_mode="reference_guided",
        client=SimpleNamespace(responses=response),
    )
    system, user = response.calls[0]["input"]
    assert system["content"][0]["text"] == SYSTEM_PROMPT or "not shown to you" in system["content"][0]["text"]
    assert user["content"][0]["text"].endswith("Natural outfit display with clear cuts.")
    assert result[0].planner_version == "allwe-ootd-sol-simple-v1"


def test_dynamic_variants_do_not_assume_unseen_products_or_fixed_10s():
    p = dynamic_ootd_prompt(
        duration_seconds=15, storyboard_count=2, product_visible_to_sol=False,
    )
    assert "Over 15 seconds" in p
    assert "15-second silent 9:16" in p
    assert "ordered optional later-shot" in p
    assert "not shown to you" in p
    assert "a 10-second silent 9:16" not in p
