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



@pytest.mark.asyncio
async def test_explicit_frame_and_mixed_reference_roles_follow_wire_order(
    tmp_path: Path,
) -> None:
    """Roles must follow actual contents, not a storyboard-only count."""
    first = _image(tmp_path / "explicit-first.png")
    last = _image(tmp_path / "explicit-last.png")
    storyboard = _image(tmp_path / "extra-storyboard.png")
    character = _image(tmp_path / "extra-reference.png")
    product = _image(tmp_path / "product-front.png")
    req = VideoGenerationRequest(
        model="kling-3.0-omni",
        first_frame_path=first,
        last_frame_path=last,
        image_paths=(storyboard,),
        refer_image_paths=(character,),
        product_reference_paths=(product,),
        prompt="起点图一，尾帧图二，分镜@image3，人物@image4，商品图五。",
        duration=10, aspect_ratio="9:16", multi_shot=True, audio="off",
        external_task_id="offline-mixed-slot-check",
        director_native_profile="allwe-ootd-sol-simple-v1",
    )
    payload = await _adapter(tmp_path).build_request_payload(req)
    assert [(item["type"], item["id"]) for item in payload["contents"][1:]] == [
        ("first_frame", "image_1"),
        ("last_frame", "image_2"),
        ("refer_image", "image_3"),
        ("refer_image", "image_4"),
        ("refer_image", "image_5"),
    ]
    sent = payload["contents"][0]["text"]
    for number, intent in [
        (1, "唯一视频起始首帧"), (2, "视频结束帧"),
        (3, "后续分镜参考图"), (4, "其他视觉补充参考图"),
        (5, "商品主参考图"),
    ]:
        assert f"@image_{number}={intent}" in sent
    assert req.prompt == "起点图一，尾帧图二，分镜@image3，人物@image4，商品图五。"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [
    "allwe-ootd-sol-cross-scene-b-v1",
    "allwe-ootd-sol-dynamic-display-v1",
])
async def test_new_director_profiles_use_lossless_native_shot_compilation(
    tmp_path: Path, profile: str,
) -> None:
    req = _request(
        tmp_path,
        "镜头一0-3秒：人物整理衣角。镜头二3-10秒：近景自然展示印花。",
        profile=profile,
    )
    payload = await _adapter(tmp_path).build_request_payload(req)
    text = payload["contents"][0]["text"]
    assert text.startswith("shot 1, 3,")
    assert "shot 2, 7," in text


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
async def test_missing_api_image_reference_fails_before_paid_submission(tmp_path: Path) -> None:
    """Unknown ID must not be silently forwarded to the paid API."""
    adapter = _adapter(tmp_path)
    with pytest.raises(KlingApiError, match="unbound image reference: image_9"):
        await adapter.build_request_payload(_request(tmp_path, "首帧@image_9展示图案"))


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
async def test_mixed_aliases_with_unbound_reference_fail_validation(tmp_path: Path) -> None:
    with pytest.raises(KlingApiError, match="unbound image reference: image_9"):
        await _adapter(tmp_path).build_request_payload(
            _request(tmp_path, "首帧图一，之后@image9展示背面")
        )


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
