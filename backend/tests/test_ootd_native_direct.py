from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from libtv_agent.adapters.kling_api_ootd import api_ootd_prompt
from libtv_agent.adapters.kling_official_api import KlingOfficialApiAdapter, KlingApiError
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import CreativePlannerError
from libtv_agent.services.ootd_native_direct import (
    NATIVE_DIRECT_PROFILE, NATIVE_DIRECT_SYSTEM, validate_native_direct,
)
from libtv_agent.services.ootd_simple import active_ootd_profile, plan_simple_ootd

SOURCE = (
    "shot 1, 3, Start exactly from @image_1. Natural American smartphone UGC. "
    "She smiles with the original garment from @image_3 fully visible;\n"
    "shot 2, 4, Cut to a straight-on detail of the same garment. Preserve the "
    "exact print and lettering in @image_3;\n"
    "shot 3, 3, Cut to @image_2 with the same person and outfit. Silent, no overlays;"
)


def validate(source=SOURCE, repair=False):
    return validate_native_direct(source, duration=10, image_count=3,
                                  storyboard_count=2, allow_final_separator_repair=repair)


def settings(**kw):
    return Settings(app_env="test", kling_provider_backend="api",
                    kling_api_model="kling-3.0-omni", openai_model="gpt-6.2-sol",
                    _env_file=None, **kw)


@pytest.mark.parametrize("source,reason", [
    (SOURCE.replace("shot 2, 4,", "shot 3, 4,"), "OOTD_NATIVE_SHOT_SEQUENCE"),
    (SOURCE.replace("shot 2, 4,", "shot 2, 5,"), "OOTD_NATIVE_DURATION"),
    (SOURCE.replace("@image_3", "@image_4"), "OOTD_NATIVE_IMAGE_REFERENCE"),
    (SOURCE.replace("@image_3", "@image_03"), "OOTD_NATIVE_IMAGE_REFERENCE"),
    (SOURCE.replace("@image_1", "@image_2"), "OOTD_NATIVE_FIRST_FRAME"),
    (SOURCE.replace("@image_3", "product photo"), "OOTD_NATIVE_PRODUCT_REFERENCE_MISSING"),
    (SOURCE.replace("Natural American", "a"*513), "OOTD_NATIVE_SHOT_LENGTH"),
    (SOURCE[:-1], "OOTD_NATIVE_TERMINATOR_MISSING"),
    ("Global preamble. "+SOURCE, "OOTD_NATIVE_SHOT_FORMAT"),
    (SOURCE.replace("She smiles", "**She smiles**"), "OOTD_NATIVE_MARKDOWN"),
])
def test_invalid_drafts(source, reason):
    assert validate(source) == (source, reason)


def test_valid_and_separator_only():
    assert validate() == (SOURCE, None)
    assert validate(SOURCE[:-1], True) == (SOURCE, None)
    invalid = SOURCE[:-1].replace("shot 2, 4,", "shot 2, 5,")
    assert validate(invalid, True) == (invalid, "OOTD_NATIVE_DURATION")


def test_profile_selection_and_rollback(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_NATIVE_DIRECT_ENABLED", "1")
    args = dict(video_type="ootd", director_mode="cross_scene_v2",
                product_count=1, analyze_product_images=True)
    assert active_ootd_profile(settings(), **args) == NATIVE_DIRECT_PROFILE
    for changes in ({"video_type":"unboxing"}, {"director_mode":"reference_guided"},
                    {"product_count":0}, {"analyze_product_images":False}):
        assert active_ootd_profile(settings(), **{**args, **changes}) != NATIVE_DIRECT_PROFILE
    monkeypatch.setenv("ALLWE_OOTD_NATIVE_DIRECT_ENABLED", "0")
    assert active_ootd_profile(settings(), **args) != NATIVE_DIRECT_PROFILE


@pytest.mark.asyncio
@pytest.mark.parametrize("drafts,calls", [
    ([SOURCE], 1), ([SOURCE[:-1]], 1), (["wrong", SOURCE], 2),
])
async def test_planner_direct_format(monkeypatch, drafts, calls):
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_NATIVE_DIRECT_ENABLED", "1")
    fake = AsyncMock(side_effect=[SimpleNamespace(output_text=d) for d in drafts])
    monkeypatch.setattr("libtv_agent.services.ootd_simple.create_sol_response", fake)
    result = await plan_simple_ootd(
        settings=settings(), storyboard_images=[
            AnalysisImageInput(asset_id="s1", order=1, role="STORYBOARD", image_url="fixture:s1"),
            AnalysisImageInput(asset_id="s2", order=2, role="STORYBOARD", image_url="fixture:s2"),
        ], product_images=[
            AnalysisImageInput(asset_id="p", order=1, role="PRODUCT", image_url="fixture:p"),
        ], analyze_product_images=True, creative_brief="", duration_seconds=10, client=object(),
    )
    assert result[0].prompt == SOURCE
    assert result[0].planner_version == NATIVE_DIRECT_PROFILE
    assert result[0].request_attempts == calls
    assert fake.await_count == calls
    assert fake.call_args.kwargs["input"][0]["content"][0]["text"] == NATIVE_DIRECT_SYSTEM


@pytest.mark.asyncio
async def test_native_provider_wire_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLWE_KLING_TRANSLATOR_LITE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_KLING_TRANSLATOR_LITE_DISABLE_FILE", str(tmp_path/"absent"))
    paths = tuple(tmp_path/f"{n}.png" for n in range(3))
    for path in paths:
        Image.new("RGB", (320, 320), "green").save(path)
    req = VideoGenerationRequest(
        prompt=SOURCE, model="kling-3.0-omni", image_paths=paths[:2],
        product_reference_paths=paths[2:], duration=10, aspect_ratio="9:16",
        multi_shot=True, audio="off", external_task_id="native-test-only",
        director_native_profile=NATIVE_DIRECT_PROFILE,
    )
    assert api_ootd_prompt(req) == SOURCE
    adapter = KlingOfficialApiAdapter(settings(kling_api_enabled=True, kling_api_key="offline-test-key"))
    payload = await adapter.build_request_payload(req)
    assert payload["contents"][0]["text"] == SOURCE
    assert [item["id"] for item in payload["contents"][1:]] == ["image_1", "image_2", "image_3"]
    assert payload["settings"]["multi_shot"] is True
    assert (await adapter.request_summary(req))["promptLength"] == len(SOURCE)
    from dataclasses import replace
    with pytest.raises(KlingApiError):
        await adapter.build_request_payload(replace(req, prompt=SOURCE.replace("@image_3", "@image_4")))

@pytest.mark.parametrize("labels", [
    ("00:00–00:03. ", "00:03–00:07. ", "00:07–00:10. "),
    ("0–3s. ", "3–7s. ", "7–10s. "),
])
def test_remove_only_redundant_leading_time_labels(labels):
    timestamped = SOURCE
    for heading, label in zip(("shot 1, 3, ", "shot 2, 4, ", "shot 3, 3, "), labels):
        timestamped = timestamped.replace(heading, heading + label)
    result = validate_native_direct(
        timestamped, duration=10, image_count=3, storyboard_count=2,
        allow_redundant_timestamp_repair=True,
    )
    assert result == (SOURCE, None)
    conflict = timestamped.replace(labels[1], "00:03–00:06. ")
    assert validate_native_direct(
        conflict, duration=10, image_count=3, storyboard_count=2,
        allow_redundant_timestamp_repair=True,
    ) == (conflict, "OOTD_NATIVE_TIMESTAMP_CONFLICT")


def test_preserve_timing_inside_actions():
    text = SOURCE.replace("She smiles", "She smiles for 1–2s, then relaxes")
    assert validate_native_direct(
        text, duration=10, image_count=3, storyboard_count=2,
        allow_redundant_timestamp_repair=True,
    ) == (text, None)


@pytest.mark.asyncio
async def test_planner_removes_timestamp_without_retry(monkeypatch):
    monkeypatch.setenv("ALLWE_OOTD_DYNAMIC_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_OOTD_NATIVE_DIRECT_ENABLED", "1")
    raw = SOURCE.replace("shot 1, 3, ", "shot 1, 3, 00:00–00:03. ")
    fake = AsyncMock(return_value=SimpleNamespace(output_text=raw))
    monkeypatch.setattr("libtv_agent.services.ootd_simple.create_sol_response", fake)
    result = await plan_simple_ootd(
        settings=settings(), storyboard_images=[
            AnalysisImageInput(asset_id="s1", order=1, role="STORYBOARD", image_url="fixture:s1"),
            AnalysisImageInput(asset_id="s2", order=2, role="STORYBOARD", image_url="fixture:s2"),
        ], product_images=[
            AnalysisImageInput(asset_id="p", order=1, role="PRODUCT", image_url="fixture:p"),
        ], analyze_product_images=True, creative_brief="", duration_seconds=10, client=object(),
    )
    assert result[0].prompt == SOURCE
    assert fake.await_count == 1
    system, user = fake.call_args.kwargs["input"]
    assert "Do not repeat start/end" in system["content"][0]["text"]
    assert "shot durations MUST total" in user["content"][0]["text"]
    assert "start/end timestamps MUST" not in user["content"][0]["text"]
