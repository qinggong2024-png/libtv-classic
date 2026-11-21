"""Kling 3.0 Omni API-only formatting for the proven OOTD Sol director.

The source prompt is never edited or persisted differently. A provider-facing
format is derived only when its shot timing is unambiguous and lossless.
"""
from __future__ import annotations

import re

from libtv_agent.adapters.video_provider import VideoGenerationRequest

_OOTD_PROFILES = frozenset((
    "allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2",
))
_IMAGE_TOKEN = re.compile(r"@image_(\d+)(?!\d)|图片(\d+)(?!\d)")
_SHOT_HEADING = re.compile(
    r"镜头([一二三四五六1-6])\s*"
    r"(\d+)\s*[-–—~至]\s*(\d+)\s*秒\s*[:：]?"
)
_SHOT_MARK = re.compile(r"镜头[一二三四五六1-6]")
_CHINESE_INDEX = {name: i for i, name in enumerate("一二三四五六", 1)}


def _ootd_role_header(request: VideoGenerationRequest) -> str:
    """Give each ordered input one role without relying on CLI internals."""
    frames = len(request.image_paths)
    products = len(request.product_reference_paths)
    if frames <= 1 and products <= 1:
        return request.prompt
    roles = ["图片1=唯一视频起始首帧"]
    roles.extend(f"图片{i}=后续镜头参考" for i in range(2, frames + 1))
    roles.extend(
        f"图片{frames + i}=" + ("商品主参考图" if i == 1 else "商品细节补充参考图")
        for i in range(1, products + 1)
    )
    guard = (
        "【图片角色，按上传顺序】" + "；".join(roles)
        + "。首帧以图片1的原始画面起镜，其他分镜只辅助后续构图、动作及景别，"
        "不要求逐张定格或强制固定切镜。商品图片仅控制衣服外观、文字、"
        "花纹与材质，不替换人物及场景。"
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
    """Bind image IDs to the API contents order; leave CLI and 4.0 untouched."""
    if request.director_native_profile not in _OOTD_PROFILES:
        return request.prompt
    if len(request.image_paths) > 1 or len(request.product_reference_paths) > 1:
        prompt = _ootd_role_header(request)
    else:
        prompt = request.prompt

    image_count = (
        int(bool(request.first_frame_path)) + len(request.image_paths)
        + int(bool(request.last_frame_path)) + len(request.refer_image_paths)
        + len(request.product_reference_paths)
    )
    # In normal OOTD tasks the first image_path becomes first_frame, not a
    # duplicate. Each ordered input corresponds to exactly one contents ID.
    def bind(match: re.Match[str]) -> str:
        number = int(match.group(1) or match.group(2))
        if not 1 <= number <= image_count:
            raise ValueError(f"OOTD references nonexistent API image_{number}")
        return f"@image_{number}"

    prompt = _IMAGE_TOKEN.sub(bind, prompt)
    if request.multi_shot is False:
        return prompt
    return _native_shots_if_safe(prompt, request.duration)
