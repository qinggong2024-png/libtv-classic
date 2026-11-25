"""Production wiring regression: offline-only Sol -> Kling Lite release switch."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from libtv_agent.adapters.kling_api_ootd import api_ootd_prompt, _native_shots_if_safe
from libtv_agent.adapters.kling_official_api import KlingOfficialApiAdapter
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings


SOURCE = (
    "保持商品印花、人物、光线不变。全程不要增加字幕。\n\n"
    "0.0–3.0秒：镜自拍中景，展示前胸印花。\n"
    "3.0–6.4秒：直接切镜（DIRECT CUT），镜外腰至膝上近景。\n"
    "6.4–10.0秒：直接切镜（DIRECT CUT），展示原衣背面。"
)


def req(prompt: str = SOURCE, *, profile: str = "allwe-ootd-sol-dynamic-display-v1",
        multi_shot: bool | None = True, with_multiref: bool = False) -> VideoGenerationRequest:
    return VideoGenerationRequest(
        prompt=prompt, model="kling-3.0-omni", duration=10,
        multi_shot=multi_shot, audio="off", aspect_ratio="9:16",
        director_native_profile=profile,
        image_paths=(Path("a.jpg"), Path("b.jpg")) if with_multiref else (Path("a.jpg"),),
        product_reference_paths=(Path("product-front.jpg"), Path("product-back.jpg"))
        if with_multiref else (Path("product.jpg"),),
    )


def on(monkeypatch, tmp_path: Path) -> Path:
    marker = tmp_path / "disable-lite.flag"
    monkeypatch.setenv("ALLWE_KLING_TRANSLATOR_LITE_ENABLED", "1")
    monkeypatch.setenv("ALLWE_KLING_TRANSLATOR_LITE_DISABLE_FILE", str(marker))
    return marker


def test_live_lite_integer_cut_schedule_preserves_global_context_and_source(
    monkeypatch, tmp_path,
):
    on(monkeypatch, tmp_path)
    request = req()
    translated = api_ootd_prompt(request)
    assert translated.startswith("保持商品印花、人物、光线不变。全程不要增加字幕。")
    assert "SHOT 1 [0–3s]" in translated
    assert "SHOT 2 [3–6s] | HARD CUT, OFF-MIRROR VIEW, CLOSE SHOT" in translated
    assert "SHOT 3 [6–10s]" in translated
    assert "shot 1, " not in translated  # Scoped global preamble; never falsely native.
    assert translated.count("DIRECT CUT") == 2
    assert request.prompt == SOURCE


def test_production_off_switch_uses_exact_legacy_formatter(monkeypatch, tmp_path):
    marker = on(monkeypatch, tmp_path)
    enabled = api_ootd_prompt(req())
    marker.write_text("operator rollback", encoding="utf-8")
    disabled = api_ootd_prompt(req())
    assert enabled != disabled
    assert disabled == _native_shots_if_safe(SOURCE, 10)
    assert disabled.startswith("shot 1, 3,")
    assert "shot 3, 4," in disabled
    marker.unlink()
    assert api_ootd_prompt(req()) == enabled


def test_unset_feature_flag_keeps_legacy(monkeypatch, tmp_path):
    marker = on(monkeypatch, tmp_path)
    monkeypatch.delenv("ALLWE_KLING_TRANSLATOR_LITE_ENABLED")
    assert api_ootd_prompt(req()) == _native_shots_if_safe(SOURCE, 10)


@pytest.mark.parametrize("profile,multi_shot", [
    ("some-other-director", True),
    ("allwe-ootd-sol-dynamic-display-v1", False),
])
def test_non_ootd_or_disabled_multishot_never_compiles(monkeypatch, tmp_path, profile, multi_shot):
    on(monkeypatch, tmp_path)
    request = req(profile=profile, multi_shot=multi_shot)
    assert api_ootd_prompt(request) == SOURCE


def test_role_header_is_not_moved_inside_first_shot(monkeypatch, tmp_path):
    on(monkeypatch, tmp_path)
    request = req(with_multiref=True)
    final = api_ootd_prompt(request)
    assert final.startswith("【图片角色，按上传顺序】")
    assert "SHOT 1 [0–3s]" in final
    assert "SHOT 2 [3–6s]" in final
    assert "SHOT 3 [6–10s]" in final
    assert final.count("【图片角色") == 1
    assert request.prompt == SOURCE


def test_invalid_sol_shots_are_passed_through_without_second_compiler(
    monkeypatch, tmp_path,
):
    on(monkeypatch, tmp_path)
    source = "0–3秒：正面。\n4–10秒：背面。"
    assert api_ootd_prompt(req(source)) == source


@pytest.mark.asyncio
async def test_actual_offline_provider_payload_images_untouched_and_summary_equal(
    monkeypatch, tmp_path,
):
    on(monkeypatch, tmp_path)
    a = tmp_path / "first.png"
    p = tmp_path / "garment.png"
    Image.new("RGB", (400, 600), "cyan").save(a)
    Image.new("RGB", (600, 400), "red").save(p)
    request = VideoGenerationRequest(
        prompt=SOURCE, model="kling-3.0-omni", duration=10, multi_shot=True,
        audio="off", aspect_ratio="9:16", external_task_id="offline-release-test",
        director_native_profile="allwe-ootd-sol-dynamic-display-v1",
        image_paths=(a,), product_reference_paths=(p,),
    )
    adapter = KlingOfficialApiAdapter(Settings(
        app_env="development", runner_root=tmp_path / "runner",
        artifact_root=tmp_path / "artifacts",
        kling_api_enabled=True, kling_api_key="offline-test-key",
        kling_api_model="kling-3.0-omni", _env_file=None,
    ))
    payload = await adapter.build_request_payload(request)
    info = await adapter.request_summary(request)
    assert payload["contents"][0]["text"].startswith("保持商品")
    assert "SHOT 2 [3–6s]" in payload["contents"][0]["text"]
    assert [v["type"] for v in payload["contents"][1:]] == [
        "first_frame", "refer_image"
    ]
    assert payload["settings"]["duration"] == 10
    assert payload["settings"]["multi_shot"] is True
    assert info["promptLength"] == len(payload["contents"][0]["text"])
    assert request.prompt == SOURCE
