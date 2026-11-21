"""Kling 3.0 Omni API-only formatting for the proven OOTD Sol director.

The source prompt is never edited or persisted differently. A provider-facing
format is derived only when its shot timing is unambiguous and lossless.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from libtv_agent.adapters.video_provider import VideoGenerationRequest

_OOTD_PROFILES = frozenset((
    "allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2",
    "allwe-ootd-sol-cross-scene-b-v1", "allwe-ootd-sol-dynamic-display-v1",
))
_LOG = logging.getLogger(__name__)
# Both common Sol descriptions and official API-style tokens are text aliases.
# Only the official @image_N token is sent when a matching contents ID exists.
_IMAGE_TOKEN = re.compile(r"@image_?(\d+)(?!\d)|图片?(\d+|[一二三四五六七八九十]+)(?![\d一二三四五六七八九十])")
_IMAGE_CHINESE_INDEX = {name: index for index, name in enumerate("一二三四五六七八九", 1)}
_IMAGE_CHINESE_INDEX["十"] = 10
_SHOT_HEADING = re.compile(
    r"镜头([一二三四五六1-6])\s*"
    r"(\d+)\s*[-–—~至]\s*(\d+)\s*秒\s*[:：]?"
)
_SHOT_MARK = re.compile(r"镜头[一二三四五六1-6]")
_CHINESE_INDEX = {name: i for i, name in enumerate("一二三四五六", 1)}


class UnboundImageReference(ValueError):
    """A prompt references an image slot not actually included in provider contents."""


@dataclass(frozen=True)
class KlingImageSlot:
    role: str
    path: Path
    intent: str


def kling_ordered_image_slots(request: VideoGenerationRequest) -> tuple[KlingImageSlot, ...]:
    """One authoritative slot order for both the wire payload and text aliases.

    Keep this synchronized by *using* it in the official adapter, rather than
    independently estimating image IDs from storyboard/product counts.
    """
    legacy = list(request.image_paths)
    opening = request.first_frame_path
    if opening is None and legacy:
        opening = legacy.pop(0)
    slots: list[KlingImageSlot] = []
    if opening is not None:
        slots.append(KlingImageSlot("first_frame", opening, "唯一视频起始首帧"))
    if request.last_frame_path is not None:
        slots.append(KlingImageSlot("last_frame", request.last_frame_path, "视频结束帧"))
    slots.extend(KlingImageSlot("refer_image", path, "后续分镜参考图") for path in legacy)
    slots.extend(
        KlingImageSlot("refer_image", path, "其他视觉补充参考图")
        for path in request.refer_image_paths
    )
    slots.extend(
        KlingImageSlot(
            "refer_image", path,
            "商品主参考图" if index == 1 else "商品细节补充参考图",
        )
        for index, path in enumerate(request.product_reference_paths, start=1)
    )
    return tuple(slots)


def _ootd_role_header(request: VideoGenerationRequest) -> str:
    """Describe the *actual* contents order, including explicit first/last frames."""
    needs_header = (
        len(request.image_paths) > 1
        or len(request.product_reference_paths) > 1
        or bool(request.refer_image_paths)
        or request.last_frame_path is not None
        or (request.first_frame_path is not None and bool(request.image_paths))
    )
    if not needs_header:
        return request.prompt
    slots = kling_ordered_image_slots(request)
    roles = [f"图片{index}={slot.intent}" for index, slot in enumerate(slots, start=1)]
    guard = (
        "【图片角色，按实际 API 图片顺序】" + "；".join(roles)
        + "。起始首帧控制开场人物与场景，后续分镜参考图保持其真实取景含义；"
        "是否使用后续分镜由导演模式决定。商品参考图只约束服装设计、文字、"
        "颜色和材质，不代替场景与人物。"
    )
    return f"{guard}\n\n{request.prompt}"


def _native_shots_if_safe(prompt: str, duration: int | None) -> str:
    """Convert fully timed integer shots; otherwise keep every Sol word intact.

    Never round fractional timestamps, guess missing time spans, drop a preamble,
    invent a shot, or truncate words to satisfy the 512-char shot limit.
    """
    if duration is None:
        return prompt
    matches = list(_SHOT_HEADING.finditer(prompt))
    if not 2 <= len(matches) <= 6 or len(_SHOT_MARK.findall(prompt)) != len(matches):
        return prompt
    converted: list[str] = []
    previous_end = 0
    for index, match in enumerate(matches, 1):
        number = _CHINESE_INDEX.get(match.group(1), match.group(1))
        start, end = int(match.group(2)), int(match.group(3))
        if int(number) != index or start != previous_end or end <= start:
            return prompt
        stop = matches[index].start() if index < len(matches) else len(prompt)
        text = prompt[match.end():stop].strip()
        if index == 1:
            text = (prompt[:match.start()].strip() + " " + text).strip()
        text = text.rstrip("；; ")
        if not text or len(text) > 512:
            return prompt
        converted.append(f"shot {index}, {end - start}, {text}")
        previous_end = end
    if previous_end != duration:
        return prompt
    return "; ".join(converted) + ";"


def api_ootd_prompt(request: VideoGenerationRequest) -> str:
    """Compile Kling 3.0 Omni image aliases while preserving Sol's source text.

    Unknown image IDs fail before a paid submission; other optional formatting
    failures fall back to the source prompt. The wire image order is not changed.
    """
    original = request.prompt
    try:
        prompt = (
            _ootd_role_header(request)
            if request.director_native_profile in _OOTD_PROFILES else original
        )

        image_count = len(kling_ordered_image_slots(request))

        def bind(match: re.Match[str]) -> str:
            reference = match.group(1) or match.group(2)
            if reference.isdecimal():
                number = int(reference)
            else:
                number = _IMAGE_CHINESE_INDEX.get(reference, 0)
            if not 1 <= number <= image_count:
                raise UnboundImageReference(f"unbound image reference: image_{number}")
            return f"@image_{number}"

        # No matching aliases is a normal case: keep the prompt as-is.
        converted = _IMAGE_TOKEN.sub(bind, prompt)
        if len(converted) > 3072:
            raise ValueError("image reference rewrite exceeds Kling 3.0 prompt limit")
        if request.director_native_profile in _OOTD_PROFILES and request.multi_shot is not False:
            converted = _native_shots_if_safe(converted, request.duration)
        if len(converted) > 3072:
            raise ValueError("shot formatting exceeds Kling 3.0 prompt limit")
        return converted
    except UnboundImageReference:
        # Never send a misleading or missing image ID to a paid provider request.
        raise
    except Exception as exc:
        # Reference normalization is optional. Leave regular provider validation
        # (file existence, image constraints, model settings) unchanged.
        _LOG.warning(
            "KLING_IMAGE_REFERENCE_NORMALIZE_FALLBACK: using original prompt; "
            "reason=%s",
            type(exc).__name__,
        )
        return original
