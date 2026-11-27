"""Native V1.2: Sol directs, the API validates and passes the exact shots."""
from __future__ import annotations

import os
import re

NATIVE_DIRECT_PROFILE = "allwe-ootd-sol-native-direct-v1.2"
NATIVE_DIRECT_BRIEF = "Natural American UGC OOTD apparel display."
NATIVE_DIRECT_SYSTEM = "### Role and Objective\nYou are an OOTD video director specializing in authentic American social media UGC.\nCreate natural, engaging, fast-paced apparel showcase videos that highlight the garment's design, fit, texture, and personal style.\n\n### Product and References\nThe original product images are the sole authority for garment appearance. Preserve the exact artwork, lettering, colors, material, construction, fit, and proportions.\nThe first storyboard image is the exact opening frame. Additional storyboard images are references for later shots.\nMaintain the same person and complete outfit. Keep the garment naturally draped and its key design clearly visible. Avoid pulling, stretching, or obstructing the garment. Show only reference-supported garment views.\n\n### UGC Style and Pacing\nUse authentic American smartphone filming aesthetics with natural, relaxed, lively human behavior.\nStart with an engaging visual moment and reveal the garment's appeal early.\nBuild a brisk rhythm through meaningful changes in action, framing, camera distance, and visual focus.\nMove to the next shot once the current shot has fulfilled its visual purpose. Allow sufficient viewing time for important artwork and lettering.\n\n### Creative Freedom\nChoose shot count, duration, actions, camera movement, and locations based on the garment and supplied references.\nFavor simple, natural actions at normal human speed. Allow casual lifestyle interaction without forcing a storyline, prop interaction, or complex performance.\n\n### Output\n\nReturn only the complete English video generation prompt in Kling's native multi-shot format:\n\nshot n, m, words;\n\nChoose the number of shots and their durations freely according to the garment, references, and requested video length.\n\nUse consecutive shot numbers and positive whole-second durations that add up exactly to the requested total duration. The duration field is the only shot timing label. Do not repeat start/end time ranges or timestamps at the beginning of words.\n\nKeep each shot description concise and within 512 characters.\n\nEach shot should contain the relevant subject action, framing, camera movement, and visual result.\n\nApply essential garment accuracy, character consistency, and UGC style instructions to the relevant shots. Only the first shot must start exactly from @image_1.\n\nUse the actual @image_N reference bindings.\n\nUse semicolons only as shot separators.\n\nDo not output a global preamble, Markdown, JSON, explanations, or additional commentary."

_SHOT = re.compile(r"\s*shot ([1-9][0-9]*), ([1-9][0-9]*), (.+?)\s*", re.S)
_REF = re.compile(r"@image_([1-9][0-9]*)(?![\w])")
_ANY_IMAGE = re.compile(r"@image[\w-]*", re.I)
# Only leading, explicit timing labels; never strip time-related action text.
_TIME = r"(?:[0-9]{1,2}:)?[0-9]{1,2}(?:\.0+)?"
_LEADING_TIME_RANGE = re.compile(
    rf"(?P<start>{_TIME})(?P<s1>s)?[ \t]*[-–—][ \t]*"
    rf"(?P<end>{_TIME})(?P<s2>s)?[ \t]*[.:：|｜–—][ \t]+",
    re.I,
)


def _whole_seconds(value: str) -> int:
    values = value.split(":")
    seconds = int(values[-1].split(".")[0])
    return int(values[0]) * 60 + seconds if len(values) == 2 else seconds


def native_direct_enabled() -> bool:
    return os.environ.get("ALLWE_OOTD_NATIVE_DIRECT_ENABLED", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }


def validate_native_direct(
    prompt: str, *, duration: int, image_count: int, storyboard_count: int,
    allow_final_separator_repair: bool = False,
    allow_redundant_timestamp_repair: bool = False,
) -> tuple[str, str | None]:
    """Check format; optionally remove redundant labels without changing actions."""
    if not prompt or not 1 <= storyboard_count <= image_count <= 7:
        return prompt, "OOTD_NATIVE_INVALID_INPUT"
    text = prompt
    if not text.endswith(";"):
        if not allow_final_separator_repair:
            return prompt, "OOTD_NATIVE_TERMINATOR_MISSING"
        text += ";"
    if len(text) > 3072:
        return prompt, "OOTD_PROMPT_TOO_LONG"
    if "**" in text or "```" in text:
        return prompt, "OOTD_NATIVE_MARKDOWN"
    parts = text.split(";")[:-1]
    if not 1 <= len(parts) <= 6:
        return prompt, "OOTD_NATIVE_SHOT_COUNT"
    total = 0
    all_refs: set[int] = set()
    rendered_parts = []
    for index, part in enumerate(parts, 1):
        match = _SHOT.fullmatch(part)
        if not match:
            return prompt, "OOTD_NATIVE_SHOT_FORMAT"
        number, seconds, words = match.groups()
        if len(number) > 1 or int(number) != index or len(seconds) > 2:
            return prompt, "OOTD_NATIVE_SHOT_SEQUENCE"
        stamp = _LEADING_TIME_RANGE.match(words)
        if stamp and (":" in stamp.group("start") or ":" in stamp.group("end")
                      or stamp.group("s1") or stamp.group("s2")):
            if (_whole_seconds(stamp.group("start")) != total
                    or _whole_seconds(stamp.group("end")) != total + int(seconds)):
                return prompt, "OOTD_NATIVE_TIMESTAMP_CONFLICT"
            if allow_redundant_timestamp_repair:
                words = words[stamp.end():]
                part = part[:match.start(3)] + words + part[match.end(3):]
        rendered_parts.append(part)
        if not words.strip() or len(words) > 512:
            return prompt, "OOTD_NATIVE_SHOT_LENGTH"
        total += int(seconds)
        refs = _REF.findall(words)
        if [f"@image_{ref}" for ref in refs] != _ANY_IMAGE.findall(words):
            return prompt, "OOTD_NATIVE_IMAGE_REFERENCE"
        if any(len(ref) > 1 or not 1 <= int(ref) <= image_count for ref in refs):
            return prompt, "OOTD_NATIVE_IMAGE_REFERENCE"
        numbers = {int(ref) for ref in refs}
        if index == 1 and 1 not in numbers:
            return prompt, "OOTD_NATIVE_FIRST_FRAME"
        all_refs.update(numbers)
    if total != duration:
        return prompt, "OOTD_NATIVE_DURATION"
    if image_count > storyboard_count and not any(i > storyboard_count for i in all_refs):
        return prompt, "OOTD_NATIVE_PRODUCT_REFERENCE_MISSING"
    return ";".join(rendered_parts) + ";", None
