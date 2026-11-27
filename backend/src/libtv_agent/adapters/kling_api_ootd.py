"""Kling 3.0 Omni API-only formatting for the proven OOTD Sol director.

The source prompt is never edited or persisted differently. A provider-facing
format is derived only when its shot timing is unambiguous and lossless.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from libtv_agent.adapters.video_provider import VideoGenerationRequest

_OOTD_PROFILES = frozenset((
    "allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2",
    "allwe-ootd-sol-cross-scene-b-v1", "allwe-ootd-sol-dynamic-display-v1",
))
_LOG = logging.getLogger(__name__)
# Both common Sol descriptions and official API-style tokens are text aliases.
# Only the official @image_N token is sent when a matching contents ID exists.
# An image ID is the provider's actual ordered contents ID, not a guessed
# human role. Constrain Chinese aliases to avoid splitting 原图一致/构图一致.
# Historical Sol outputs use 图1, 图片二 and @image_3; other common sources
# use spaced numerals, ordinals, circled digits and English image labels.
_IMAGE_NUMERAL = r"(?:\d+|[一二三四五六七八九十]+|[①②③④⑤⑥⑦⑧⑨⑩])"
_IMAGE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])@?image[ _-]*(?P<english>\d+)(?!\d)"
    r"|(?<![原构])图片?[ \t]*#?[ \t]*(?P<chinese>" + _IMAGE_NUMERAL + r")"
    r"(?![\d一二三四五六七八九十])"
    r"(?P<range>[ \t]*[-–—－~～至到][ \t]*" + _IMAGE_NUMERAL + r")?"
    r"(?!致|张|个|系列)"
    r"|第[ \t]*(?P<ordinal>" + _IMAGE_NUMERAL + r")[ \t]*(?:张|个|幅)?"
    r"[ \t]*(?:图片|图)"
    r"|(?P<first>首帧图|首图)",
    re.IGNORECASE,
)
_IMAGE_CHINESE_INDEX = {name: index for index, name in enumerate("一二三四五六七八九", 1)}
_IMAGE_CHINESE_INDEX["十"] = 10
_IMAGE_CIRCLED_INDEX = {name: index for index, name in enumerate("①②③④⑤⑥⑦⑧⑨⑩", 1)}
_IMAGE_RANGE_SUFFIX = re.compile(r"[ \t]*[-–—－~～至到][ \t]*(" + _IMAGE_NUMERAL + r")$")
_CANONICAL_IMAGE = re.compile(r"@image_\d+(?!\d)")
# Sol's real OOTD drafts mostly use line-leading unnumbered decimal ranges;
# some use numbered shots or minute:second timestamps. Recognize these formats
# without forcing Sol to write a rigid schema or changing its creative prompt.
_TIME_TOKEN = r"(?:(?:\d{1,2}:){1,2}\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?)"
_TIME_RANGE = (
    rf"(?P<start>{_TIME_TOKEN})[ \t]*(?:秒)?[ \t]*[-–—－~～至到][ \t]*"
    rf"(?P<end>{_TIME_TOKEN})(?![\d.])[ \t]*(?:秒钟?|[sS](?:ec(?:onds?)?)?)?"
)
_HEADING_END = r"[ \t]*[）)\]】]?[ \t]*(?:[:：｜|、][ \t]*)?"
_NUMBERED_HEADING = re.compile(
    r"(?:(?:镜头|分镜|(?i:shot))[ \t]*(?P<number>[一二三四五六1-6①②③④⑤⑥])"
    r"|第[ \t]*(?P<ordinal>[一二三四五六1-6])[ \t]*(?:镜头|分镜))"
    r"[ \t:：｜|（(\[【、]*" + _TIME_RANGE + _HEADING_END
)
_BARE_HEADING = re.compile(
    r"(?m)(?:^|(?<=[。；;]))[ \t]*(?:[-*•][ \t]+)?" + _TIME_RANGE + _HEADING_END
)
_ANY_TIME_RANGE = re.compile(_TIME_RANGE)
_SHOT_MARK = re.compile(
    r"(?m)(?:^|(?<=[。；;\n]))[ \t]*(?:(?:镜头|分镜|(?i:shot))[ \t]*"
    r"[一二三四五六1-6①②③④⑤⑥]|第[ \t]*[一二三四五六1-6][ \t]*(?:镜头|分镜))"
)
_CHINESE_INDEX = {name: i for i, name in enumerate("一二三四五六", 1)}
_CIRCLED_INDEX = {name: i for i, name in enumerate("①②③④⑤⑥", 1)}
_MAX_CUT_ADJUSTMENT = Decimal("0.4")  # At most 0.4 second drift per cut.


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


def _time_seconds(token: str) -> Decimal:
    """Parse plain seconds or MM:SS[.fraction], without floating-point drift."""
    fields = token.split(":")
    if len(fields) > 3:
        raise ValueError("unsupported timecode")
    total = Decimal(0)
    for index, part in enumerate(fields):
        value = Decimal(part)
        if index > 0 and value >= 60:
            raise ValueError("timecode second/minute exceeds 59")
        total = total * 60 + value
    return total


def _native_shots_if_safe(prompt: str, duration: int | None) -> str:
    """Loss-aware Sol timeline -> Kling whole-second native shot conversion.

    Supports explicit numbered shots and Sol's actual bare-line decimal/clock
    timelines. Never invent missing cuts, accept overlapping/gapped shots,
    alter creative descriptions or force an ambiguous rounding choice.
    Return the unchanged input whenever the transformation is not provably safe.
    """
    if duration is None or not isinstance(duration, int) or duration < 2:
        return prompt
    try:
        headings = sorted(
            [*_NUMBERED_HEADING.finditer(prompt), *_BARE_HEADING.finditer(prompt)],
            key=lambda match: match.start(),
        )
        if not 2 <= len(headings) <= 6:
            return prompt
        if any(a.end() > b.start() for a, b in zip(headings, headings[1:])):
            return prompt
        # If a different time-range syntax or unnumbered shot was missed, do
        # not silently attach its words to a neighboring shot.
        if len(list(_ANY_TIME_RANGE.finditer(prompt))) != len(headings):
            return prompt
        if any(
            not any(mark.start() >= shot.start() and mark.start() < shot.end()
                    for shot in headings)
            for mark in _SHOT_MARK.finditer(prompt)
        ):
            return prompt

        labels: list[str] = []
        edges: list[Decimal] = [Decimal(0)]
        for index, match in enumerate(headings, 1):
            number = match.groupdict().get("number") or match.groupdict().get("ordinal")
            if number:
                number = _CHINESE_INDEX.get(number, _CIRCLED_INDEX.get(number, number))
                if int(number) != index:
                    return prompt
            start = _time_seconds(match.group("start"))
            end = _time_seconds(match.group("end"))
            if start != edges[-1] or end <= start or end - start < 1:
                return prompt
            if end > duration:
                return prompt
            finish = headings[index].start() if index < len(headings) else len(prompt)
            description = prompt[match.end():finish].strip().rstrip("；; ")
            if index == 1:
                description = (prompt[:match.start()].strip() + " " + description).strip()
            if not description or len(description) > 512:
                return prompt
            labels.append(description)
            edges.append(end)
        if edges[-1] != duration:
            return prompt

        whole: list[int] = [0]
        for endpoint in edges[1:-1]:
            rounded = endpoint.to_integral_value(rounding=ROUND_HALF_UP)
            if abs(endpoint - rounded) > _MAX_CUT_ADJUSTMENT:
                return prompt
            whole.append(int(rounded))
        whole.append(duration)
        if any(end - start < 1 for start, end in zip(whole, whole[1:])):
            return prompt
        return "; ".join(
            f"shot {index}, {end - start}, {description}"
            for index, (start, end, description) in enumerate(
                zip(whole, whole[1:], labels), 1
            )
        ) + ";"
    except (InvalidOperation, ValueError, OverflowError, AttributeError):
        return prompt


def _image_index(text: str) -> int:
    """Only unambiguous provided image ordinals are interpreted."""
    if text.isdecimal():
        return int(text)
    return _IMAGE_CHINESE_INDEX.get(text, _IMAGE_CIRCLED_INDEX.get(text, 0))


def _image_inputs_count(request: VideoGenerationRequest) -> int:
    """Mirror KlingOfficialApiAdapter._request_image_inputs (no element IDs)."""
    # A separately supplied first frame precedes all other storyboard inputs.
    # If absent, image_paths[0] becomes the first-frame content, not an extra.
    return (
        int(request.first_frame_path is not None)
        + len(request.image_paths)
        + int(request.last_frame_path is not None)
        + len(request.refer_image_paths)
        + len(request.product_reference_paths)
    )


def _image_alias_conversion_result(
    prompt: str, request: VideoGenerationRequest,
) -> tuple[str, str, int]:
    """Pure outcome for image rewriting and verifiable wire audit.

    A bad/unbound image index rejects the entire optional rewrite, not the
    request. In-text ranges are preserved as explicit bounded endpoint refs;
    they are never guessed from garment orientation or the scene description.
    """
    matches = list(_IMAGE_TOKEN.finditer(prompt))
    if not matches:
        return prompt, "no_recognized_alias", 0
    image_count = _image_inputs_count(request)
    converted_count = 0

    try:
        def bind(match: re.Match[str]) -> str:
            nonlocal converted_count
            token = match.group()
            number_text = (
                match.group("english") or match.group("chinese")
                or match.group("ordinal")
            )
            number = _image_index(number_text) if number_text else 1
            if not 1 <= number <= image_count:
                raise ValueError("image reference is not bound in request contents")
            ranged = match.group("range")
            if ranged:
                suffix = _IMAGE_RANGE_SUFFIX.fullmatch(ranged)
                if suffix is None:
                    raise ValueError("ambiguous image range")
                end_number = _image_index(suffix.group(1))
                if not number < end_number <= image_count:
                    raise ValueError("image range is not bound in request contents")
                # Retain the author's interval notation, but bind both ends.
                joiner = re.search(r"[-–—－~～至到]", ranged)
                assert joiner is not None
                rendered = f"@image_{number}{joiner.group()}@image_{end_number}"
            else:
                rendered = f"@image_{number}"
            if rendered != token:
                converted_count += 1
            return rendered

        converted = _IMAGE_TOKEN.sub(bind, prompt)
        if len(converted) > 3072:
            raise ValueError("image reference rewrite exceeds provider prompt limit")
        return converted, (
            "converted" if converted != prompt else "already_canonical"
        ), converted_count
    except (ValueError, OverflowError, AssertionError) as exc:
        return prompt, (
            "length_guard_fallback" if "prompt limit" in str(exc)
            else "unbound_or_ambiguous_reference_fallback"
        ), 0


def _normalize_image_refs_if_safe(prompt: str, request: VideoGenerationRequest) -> str:
    """Fail-open image alias binder with no effect on Sol's stored draft."""
    try:
        converted, outcome, _ = _image_alias_conversion_result(prompt, request)
        if outcome.endswith("_fallback"):
            _LOG.warning("KLING_IMAGE_REFERENCE_NORMALIZE_FALLBACK: %s", outcome)
        return converted
    except Exception as exc:
        _LOG.warning(
            "KLING_IMAGE_REFERENCE_NORMALIZE_FALLBACK: %s", type(exc).__name__
        )
        return prompt


def api_ootd_prompt(request: VideoGenerationRequest) -> str:
    """Independently, optionally format image aliases and native shot timing.

    Only the outgoing Kling 3.0 Omni request is rewritten. On no match,
    malformed references, unsupported timing, or formatter exception, keep the
    prior valid stage output and never block submission merely for formatting.
    """
    original = request.prompt
    from libtv_agent.services.ootd_native_direct import (
        NATIVE_DIRECT_PROFILE, validate_native_direct,
    )
    if request.director_native_profile == NATIVE_DIRECT_PROFILE:
        if request.multi_shot is False or request.duration is None:
            raise ValueError("Native direct requires multi-shot and an explicit duration")
        normalized, issue = validate_native_direct(
            original, duration=request.duration,
            image_count=_image_inputs_count(request),
            storyboard_count=_image_inputs_count(request) - len(request.product_reference_paths),
        )
        if issue is not None or normalized != original:
            raise ValueError(issue or "OOTD_NATIVE_NOT_CANONICAL")
        # This is the only wire path for the new profile: no role preamble,
        # memory hint, alias rewriting or legacy translator.
        return original
    is_ootd = request.director_native_profile in _OOTD_PROFILES
    try:
        prompt = (
            _ootd_role_header(request)
            if is_ootd and (
                len(request.image_paths) > 1 or len(request.product_reference_paths) > 1
            )
            else original
        )
        if len(prompt) > 3072:
            # An optional image-role header must not cause a new input error.
            prompt = original
    except Exception as exc:
        _LOG.warning("KLING_IMAGE_ROLE_HEADER_FALLBACK: %s", type(exc).__name__)
        prompt = original

    try:
        prompt = _normalize_image_refs_if_safe(prompt, request)
    except Exception as exc:
        # Defensive boundary: even an unexpected helper failure cannot abort
        # the paid submission pipeline or suppress independent shot conversion.
        _LOG.warning("KLING_IMAGE_REFERENCE_NORMALIZE_FALLBACK: %s", type(exc).__name__)
    if not is_ootd or request.multi_shot is False:
        return prompt
    try:
        formatted = _native_shots_if_safe(prompt, request.duration)
        if len(formatted) > 3072:
            raise ValueError("native storyboard rewrite exceeds provider prompt limit")
        return formatted
    except Exception as exc:
        _LOG.warning("KLING_NATIVE_SHOTS_FALLBACK: %s", type(exc).__name__)
        return prompt
