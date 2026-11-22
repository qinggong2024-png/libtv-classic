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
    ("镜头一0-3.5秒：整理。镜头二3.5-10秒：展示。", 10),  # Ambiguous half-second.
    ("镜头一0-3.49秒：整理。镜头二3.49-10秒：展示。", 10),
    ("镜头一0-4秒：整理。镜头二5-10秒：展示。", 10),
    ("镜头一0-4秒：整理。镜头二3.9-10秒：展示。", 10),
    ("镜头一0-4秒：整理。镜头二4-9秒：展示。", 10),
    ("镜头一0-0.8秒：整理。镜头二0.8-10秒：展示。", 10),
    ("镜头一0-3秒：整理。镜头三3-10秒：展示。", 10),
    ("镜头一0-3秒：整理。镜头二3-10秒：展示。镜头三：缺少时间。", 10),
    ("模特整理衣服，直接切镜再展示。", 10),
    ("00:00–00:61秒：整理。\n00:61–00:10秒：展示。", 10),
    ("0–3秒：整理。\n3–10秒：展示。\n补充0–2秒：只是一句说明。", 10),
])
def test_ambiguous_or_incomplete_timings_are_not_rewritten(prompt: str, duration: int) -> None:
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


@pytest.mark.parametrize("source, expected", [
    ("镜头一0–3.2 秒：正面。镜头二3.2–10秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("0.0–2.6秒：正面。\n2.6–6.4秒：印花。\n6.4–10.0秒：整体穿搭。",
     ("shot 1, 3,", "shot 2, 3,", "shot 3, 4,")),
    ("10秒自然手机拍摄。\n\n0.0–3.2秒：首帧人物站稳。\n3.2–10.0秒：DIRECT CUT 印花近景。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("00:00–00:03.2｜首帧正面。\n00:03.2–00:10｜切镜印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("00:00–00:03.2秒：正面。\n00:03.2–00:10.0秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("镜头1（0–3.2秒）：正面。镜头2（3.2–10秒）：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("分镜1：0–3秒：正面。分镜2：3–10秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("第1镜头0–3秒：正面。第2镜头3–10秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("Shot 1 (0–3s): Front print. Shot 2 (3–10s): Full outfit.",
     ("shot 1, 3,", "shot 2, 7,")),
    ("镜头①（0–3秒）：正面。镜头②（3–10秒）：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("- 0–3秒：正面。\n- 3–10秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("自然手持拍摄。0.0–3.2秒：正面。3.2–10秒：切镜印花。",
     ("shot 1, 3,", "shot 2, 7,")),
    ("0秒至3秒：正面。\n3秒至10秒：印花。",
     ("shot 1, 3,", "shot 2, 7,")),
])
def test_realistic_sol_timeline_formats(source: str, expected: tuple[str, ...]) -> None:
    result = _native_shots_if_safe(source, 10)
    assert result.startswith(expected[0])
    for piece in expected:
        assert piece in result
    assert "印花" in result or "Full outfit" in result


@pytest.mark.parametrize("prompt, duration", [
    ("0.0–2.5秒：正面。\n2.5–10秒：印花。", 10),
    ("0–2秒：正面。\n2.1–10秒：印花。", 10),
    ("0–3秒：正面。\n2.9–10秒：印花。", 10),
    ("镜头一0–3秒：正面。\n镜头二4–10秒：印花。", 10),
    ("0–0.9秒：正面。\n0.9–10秒：印花。", 10),
    ("0–3.49秒：正面。\n3.49–10秒：印花。", 10),
    ("0–3秒：正面。\n3–11秒：印花。", 10),
    ("0–9.6秒：正面。\n9.6–10秒：印花。", 10),
    ("0–3秒：正面。\n3–10秒：印花。\n旁白再提 0–3 秒。", 10),
    ("0–3秒：正面。\n3–10秒：印花。\n镜头三：缺少时长", 10),
    ("0–3秒：正面。\n3–10秒：印花。" + "额外说明" * 600, 10),
])
def test_unsafe_sol_timeline_falls_back_without_truncation(
    prompt: str, duration: int
) -> None:
    assert _native_shots_if_safe(prompt, duration) == prompt


@pytest.mark.asyncio
async def test_decimal_bare_sol_shots_and_image_refs_are_independent(
    tmp_path: Path,
) -> None:
    prompt = (
        "10秒真实美式UGC拍摄。\n"
        "0.0–3.2秒：图一是唯一首帧，人物自然展示正面服装。\n"
        "3.2–10.0秒：直接切镜，参考图片2展示完整印花。"
    )
    request = _request(tmp_path, prompt, count=1, products=1, duration=10,
                       profile="allwe-ootd-sol-dynamic-display-v1")
    result = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert result.startswith("shot 1, 3,")
    assert "shot 2, 7," in result
    assert "@image_1" in result and "@image_2" in result
    assert request.prompt == prompt


@pytest.mark.asyncio
async def test_unsafe_decimal_shot_still_preserves_image_normalization(
    tmp_path: Path,
) -> None:
    prompt = "0–3.5秒：图一正面。\n3.5–10秒：图片2印花。"
    request = _request(tmp_path, prompt, profile="allwe-ootd-sol-dynamic-display-v1")
    result = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert "shot 1" not in result
    assert result == prompt.replace("图一", "@image_1").replace("图片2", "@image_2")


@pytest.mark.asyncio
@pytest.mark.parametrize("source,expected", [
    ("图 1 为唯一首帧。图片 2 是商品参考。", "@image_1 为唯一首帧。@image_2 是商品参考。"),
    ("第1张图用作首帧，第2张图片用于细节。", "@image_1用作首帧，@image_2用于细节。"),
    ("第一张图开场，第二个图片看印花。", "@image_1开场，@image_2看印花。"),
    ("Image 1 opens. image_2 is the product.", "@image_1 opens. @image_2 is the product."),
    ("@image 1开场，@image2展示商品。", "@image_1开场，@image_2展示商品。"),
    ("图①是首帧，图②作为商品参考。", "@image_1是首帧，@image_2作为商品参考。"),
    ("首图自然起镜，首帧图保留原场景。", "@image_1自然起镜，@image_1保留原场景。"),
    ("图一：首帧。图片二：商品图。", "@image_1：首帧。@image_2：商品图。"),
    ("@image_1起镜，图2是商品。", "@image_1起镜，@image_2是商品。"),
])
async def test_image_alias_variants_are_bound_to_existing_contents(
    tmp_path: Path, source: str, expected: str,
) -> None:
    request = _request(tmp_path, source)
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "图案与原图一致，构图一致，图片一致。",
    "这是一张图片，图片一张就够。",
    "与原图一致、图一系列、构图一体。",
    "原图片2要保留，原图一致。",
])
async def test_image_nonreference_chinese_not_corrupted(
    tmp_path: Path, source: str,
) -> None:
    sent = (await _adapter(tmp_path).build_request_payload(_request(tmp_path, source)))[
        "contents"
    ][0]["text"]
    assert sent == source


@pytest.mark.asyncio
async def test_image_reference_in_real_sol_instruction_keeps_ordinary_words(
    tmp_path: Path,
) -> None:
    source = "服装花纹与原图一致，构图一致，图1是首帧，图 2 是商品图。"
    sent = (await _adapter(tmp_path).build_request_payload(_request(tmp_path, source)))[
        "contents"
    ][0]["text"]
    assert sent == "服装花纹与原图一致，构图一致，@image_1是首帧，@image_2 是商品图。"


@pytest.mark.asyncio
async def test_image_range_endpoints_are_both_bound(tmp_path: Path) -> None:
    request = _request(tmp_path, "以图3–5为准，图1作为起镜。", count=2, products=3)
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert "@image_3–@image_5" in sent
    assert "以@image_3–@image_5为准" in sent
    assert "原始画面" in sent


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "图1首帧，然后图3展示背面。",
    "图1首帧，商品看图1–3。",
    "图1首帧，商品看第3张图。",
    "图1首帧，商品看图十一。",
])
async def test_unbound_image_aliases_do_not_partially_rewrite(
    tmp_path: Path, source: str,
) -> None:
    sent = (await _adapter(tmp_path).build_request_payload(_request(tmp_path, source)))[
        "contents"
    ][0]["text"]
    assert sent == source


@pytest.mark.asyncio
async def test_image_alias_converter_preserves_existing_shot_conversion(tmp_path: Path) -> None:
    prompt = (
        "原图一致，图 1 为首帧。"
        "镜头一0–3.2秒：人物自然展示印花。"
        "镜头二3.2–10秒：直接切镜，以第2张图为准。"
    )
    request = _request(
        tmp_path, prompt, profile="allwe-ootd-sol-dynamic-display-v1",
    )
    sent = (await _adapter(tmp_path).build_request_payload(request))["contents"][0]["text"]
    assert sent.startswith("shot 1, 3,")
    assert "shot 2, 7," in sent
    assert "原图一致" in sent
    assert "@image_1" in sent
    assert "@image_2" in sent
