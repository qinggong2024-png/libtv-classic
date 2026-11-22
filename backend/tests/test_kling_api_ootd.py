"""Dry-run API/OOTD contract tests. No remote provider calls."""
from __future__ import annotations

from pathlib import Path

import pytest
from libtv_agent.adapters.kling_api_ootd import _native_shots_if_safe
from libtv_agent.adapters.kling_official_api import KlingApiError, KlingOfficialApiAdapter
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings
from libtv_agent.services.ootd_simple import eligible
from PIL import Image


def _image(path: Path) -> Path:
    Image.new("RGB", (320, 320), "green").save(path)
    return path


def _adapter(tmp_path: Path) -> KlingOfficialApiAdapter:
    return KlingOfficialApiAdapter(Settings(
        app_env="development", runner_root=tmp_path / "runner",
        artifact_root=tmp_path / "artifacts", kling_api_enabled=True,
        kling_api_key="offline-test-key", kling_api_model="kling-3.0-omni",
        _env_file=None,
    ))


def _request(tmp_path: Path, prompt: str, *, count: int = 1, products: int = 1,
             duration: int = 10, model: str = "kling-3.0-omni",
             profile: str = "allwe-ootd-sol-simple-v1") -> VideoGenerationRequest:
    shots = tuple(_image(tmp_path / f"story-{i}.png") for i in range(1, count + 1))
    goods = tuple(_image(tmp_path / f"product-{i}.png") for i in range(1, products + 1))
    return VideoGenerationRequest(
        model=model, image_paths=shots, product_reference_paths=goods,
        prompt=prompt, duration=duration, aspect_ratio="9:16",
        multi_shot=True, audio="off", external_task_id="offline-ootd-check",
        director_native_profile=profile,
    )


@pytest.mark.parametrize("seconds", [5, 10, 15])
def test_ootd_eligibility_accepts_official_api_v3_omni(seconds: int) -> None:
    common = dict(generation_mode="automatic", cut_mode="explicit_cuts",
                  ai_model="gpt-6.1-sol", duration=seconds)
    assert eligible(provider="api", model="kling-3.0-omni", **common)
    assert eligible(provider="cli", model="kling-video-v3_0_omni", **common)
    assert not eligible(provider="api", model="kling-4.0", **common)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2"])
async def test_api_multireference_binding_preserves_distinct_roles(tmp_path: Path, profile: str) -> None:
    request = _request(tmp_path, "图片1开始，镜头切到图片2，衣服图案参考@image_3及图片4。",
                       count=2, products=2, profile=profile)
    adapter = _adapter(tmp_path)
    payload = await adapter.build_request_payload(request)
    assert [(item["type"], item["id"]) for item in payload["contents"][1:]] == [
        ("first_frame", "image_1"), ("refer_image", "image_2"),
        ("refer_image", "image_3"), ("refer_image", "image_4"),
    ]
    sent = payload["contents"][0]["text"]
    for n in (1, 2, 3, 4):
        assert f"@image_{n}" in sent
    assert "商品主参考" in sent
    assert "图片1" not in sent
    assert request.prompt == "图片1开始，镜头切到图片2，衣服图案参考@image_3及图片4。"
    assert payload["settings"]["multi_shot"] is True
    assert payload["options"]["watermark_info"]["enabled"] is False
    assert (await adapter.request_summary(request))["promptLength"] == len(sent)


@pytest.mark.asyncio
async def test_missing_api_image_reference_falls_back_and_still_submits(tmp_path: Path) -> None:
    """Unknown ID is logged, not a local submission blocker."""
    adapter = _adapter(tmp_path)
    prompt = "首帧@image_9展示图案"
    payload = await adapter.build_request_payload(_request(tmp_path, prompt))
    assert payload["contents"][0]["text"] == prompt
    assert [item["id"] for item in payload["contents"][1:]] == ["image_1", "image_2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["图一", "图片一", "图1", "图片1", "@image1", "@image_1"])
async def test_image_one_aliases_bind_to_first_frame(tmp_path: Path, alias: str) -> None:
    adapter = _adapter(tmp_path)
    payload = await adapter.build_request_payload(_request(tmp_path, f"开场参考{alias}展示正面"))
    assert "开场参考@image_1展示正面" in payload["contents"][0]["text"]
    assert payload["contents"][1]["type"] == "first_frame"
    assert payload["contents"][1]["id"] == "image_1"


@pytest.mark.asyncio
async def test_normalization_unmatched_tokens_do_not_block_or_change_prompt(tmp_path: Path) -> None:
    prompt = "人物自然介绍衬衫，镜头缓慢靠近"
    payload = await _adapter(tmp_path).build_request_payload(_request(tmp_path, prompt))
    assert payload["contents"][0]["text"] == prompt


@pytest.mark.asyncio
async def test_mixed_aliases_with_unbound_reference_revert_entire_original(tmp_path: Path) -> None:
    prompt = "首帧图一，之后@image9展示背面"
    payload = await _adapter(tmp_path).build_request_payload(_request(tmp_path, prompt))
    assert payload["contents"][0]["text"] == prompt


@pytest.mark.asyncio
async def test_image_aliases_also_normalize_for_other_v3_profiles(tmp_path: Path) -> None:
    prompt = "@image1开场，参考图二"
    payload = await _adapter(tmp_path).build_request_payload(
        _request(tmp_path, prompt, profile="other-director")
    )
    assert payload["contents"][0]["text"] == "@image_1开场，参考@image_2"


@pytest.mark.asyncio
async def test_normalization_overflow_falls_back_to_original_prompt(tmp_path: Path) -> None:
    prompt = "a" * 3066 + " 图一"  # The alias fits before rewriting.
    payload = await _adapter(tmp_path).build_request_payload(
        _request(tmp_path, prompt, profile="other-director")
    )
    assert payload["contents"][0]["text"] == prompt


@pytest.mark.asyncio
async def test_v3_prompt_length_uses_3072_not_v4_budget(tmp_path: Path) -> None:
    with pytest.raises(KlingApiError, match="3072"):
        await _adapter(tmp_path).build_request_payload(
            _request(tmp_path, "正常展示" * 850)
        )


def test_native_format_when_integer_shots_are_fully_grounded() -> None:
    source = (
        "9:16自然手机拍摄。镜头一0-3秒：模特从原首帧整理衣服，直接切镜。"
        "镜头二3-6秒：近景展示印花，直接切镜。"
        "镜头三6-10秒：全身展示穿搭，不添加字幕。"
    )
    rendered = _native_shots_if_safe(source, 10)
    assert rendered.startswith("shot 1, 3,")
    assert "shot 2, 3," in rendered
    assert "shot 3, 4," in rendered
    assert "模特从原首帧整理衣服" in rendered
    assert "全身展示穿搭" in rendered


@pytest.mark.parametrize("prompt,duration", [
    ("镜头一0-3.2秒：整理。镜头二3.2-10秒：展示。", 10),
    ("镜头一0-4秒：整理。镜头二5-10秒：展示。", 10),
    ("镜头一0-4秒：整理。镜头二4-9秒：展示。", 10),
    ("模特整理衣服，直接切镜再展示。", 10),
])
def test_unstructured_or_fractional_timings_are_not_rewritten(prompt: str, duration: int) -> None:
    assert _native_shots_if_safe(prompt, duration) == prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [
    "allwe-ootd-sol-cross-scene-b-v1",
    "allwe-ootd-sol-dynamic-display-v1",
])
async def test_cross_scene_and_dynamic_directors_send_native_shots(tmp_path: Path, profile: str) -> None:
    prompt = (
        "镜头一0-3秒：图一正面展示衣服印花；"
        "镜头二3-10秒：图片2展示完整服装及穿着效果。"
    )
    request = _request(tmp_path, prompt, profile=profile)
    payload = await _adapter(tmp_path).build_request_payload(request)
    sent = payload["contents"][0]["text"]
    assert sent.startswith("shot 1, 3,")
    assert "shot 2, 7," in sent
    assert "@image_1" in sent
    assert "@image_2" in sent
    assert request.prompt == prompt


@pytest.mark.asyncio
async def test_unknown_image_ref_does_not_block_valid_native_shots(tmp_path: Path) -> None:
    prompt = (
        "镜头一0-3秒：图一穿衣正面；"
        "镜头二3-10秒：@image_9 衣服印花特写。"
    )
    request = _request(tmp_path, prompt, profile="allwe-ootd-sol-dynamic-display-v1")
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent.startswith("shot 1, 3,")
    assert "shot 2, 7," in sent
    assert "图一" in sent  # Invalid image reference reverts the entire image stage.
    assert "@image_9" in sent


@pytest.mark.asyncio
async def test_invalid_shot_timing_keeps_successful_image_alias(tmp_path: Path) -> None:
    prompt = "图一正面。镜头一0-3.5秒：自然展示；镜头二3.5-10秒：完整印花。"
    request = _request(tmp_path, prompt, profile="allwe-ootd-sol-dynamic-display-v1")
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == prompt.replace("图一", "@image_1")
    assert "shot 1" not in sent


@pytest.mark.asyncio
async def test_image_normalizer_exception_does_not_block_shot_rewrite(
    tmp_path: Path, monkeypatch,
) -> None:
    import libtv_agent.adapters.kling_api_ootd as formatter

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated image converter failure")

    monkeypatch.setattr(formatter, "_normalize_image_refs_if_safe", fail)
    prompt = "镜头一0-3秒：正面；镜头二3-10秒：印花。"
    request = _request(tmp_path, prompt)
    # Unexpected exceptions at the optional image stage must also be nonblocking.
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent.startswith("shot 1, 3,")


@pytest.mark.asyncio
async def test_native_formatter_exception_preserves_image_binding(
    tmp_path: Path, monkeypatch,
) -> None:
    import libtv_agent.adapters.kling_api_ootd as formatter

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated storyboard converter failure")

    monkeypatch.setattr(formatter, "_native_shots_if_safe", fail)
    prompt = "镜头一0-3秒：图一正面；镜头二3-10秒：衣服中景。"
    request = _request(tmp_path, prompt)
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == prompt.replace("图一", "@image_1")


@pytest.mark.asyncio
async def test_optional_role_header_overflow_never_blocks_submission(tmp_path: Path) -> None:
    prompt = "完整印花与自然动作。" * 298
    assert len(prompt) < 3072
    request = _request(tmp_path, prompt, count=2, products=2)
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == prompt


@pytest.mark.asyncio
async def test_missing_match_and_non_ootd_keep_nonshot_text(tmp_path: Path) -> None:
    prompt = "正面自然展示衣服与图案，正常速度。"
    request = _request(tmp_path, prompt, profile="allwe-ootd-sol-dynamic-display-v1")
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == prompt
    other = _request(tmp_path, prompt, profile="other-director")
    sent_other = (await _adapter(tmp_path).build_request_payload(other))["contents"][0]["text"]
    assert sent_other == prompt


@pytest.mark.asyncio
async def test_native_formatter_overflow_reverts_just_native_stage(
    tmp_path: Path, monkeypatch,
) -> None:
    import libtv_agent.adapters.kling_api_ootd as formatter

    monkeypatch.setattr(formatter, "_native_shots_if_safe", lambda *_: "X" * 3100)
    prompt = "图一清楚展示衣服。"
    request = _request(tmp_path, prompt)
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == "@image_1清楚展示衣服。"
