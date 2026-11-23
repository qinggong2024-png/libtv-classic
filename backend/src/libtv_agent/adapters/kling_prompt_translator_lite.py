"""Sol -> Kling camera-language translator, isolated and provider-independent.

Sol remains the director. This formats its existing shots and never changes
actions, reference-image binding, product details, or actual Provider JSON.

Native Omni shot grammar is used for safe integer-aligned timelines without
a global preamble. Fractional boundaries are converted only under a bounded
cut-adjustment policy; global instructions stay outside per-shot text.

This module is not imported into the production submit path.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Literal

from libtv_agent.adapters.kling_api_ootd import (
    _ANY_TIME_RANGE,
    _BARE_HEADING,
    _CHINESE_INDEX,
    _CIRCLED_INDEX,
    _NUMBERED_HEADING,
    _SHOT_MARK,
    _time_seconds,
)

Mode = Literal["native_shots", "camera_language", "unchanged"]
_IMAGE = re.compile(r"@image_\d+(?!\d)")
_HARD_CUT = re.compile(r"DIRECT\s+CUT|直接切镜|硬切", re.I)
_FRAMING = (
    ("头至膝", "HEAD-TO-KNEE FRAMING"),
    ("中远景", "MEDIUM-WIDE SHOT"),
    ("全身", "FULL-BODY SHOT"),
    ("特写", "CLOSE-UP"),
    ("近景", "CLOSE SHOT"),
    ("中景", "MEDIUM SHOT"),
    ("远景", "WIDE SHOT"),
)
_MAX_NATIVE_WORDS = 512
_MAX_PROMPT = 3072
# Deterministic nearest integer cut; exact .5s ties are assigned later (ROUND_HALF_UP).
# The original cut and every signed adjustment are retained in Translation.
_MAX_CUT_SHIFT = Decimal("0.5")


@dataclass(frozen=True)
class CameraShot:
    number: int
    start: Decimal
    end: Decimal
    original_text: str
    camera_cues: tuple[str, ...]
    image_refs: tuple[str, ...]

    @property
    def duration(self) -> Decimal:
        return self.end - self.start


@dataclass(frozen=True)
class Translation:
    mode: Mode
    reason: str
    original_prompt: str
    kling_prompt: str
    shots: tuple[CameraShot, ...]
    original_sha256: str
    result_sha256: str
    original_cut_edges: tuple[str, ...] = ()
    integer_cut_edges: tuple[int, ...] = ()
    integer_shot_durations: tuple[int, ...] = ()
    cut_adjustments: tuple[tuple[str, int, str], ...] = ()

    @property
    def shot_count(self) -> int:
        return len(self.shots)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cue_tokens(body: str) -> tuple[str, ...]:
    """Only camera intent literally present in Sol's first shot sentence."""
    first = re.split(r"[。；;\n]", body, maxsplit=1)[0]
    # Shot context may be longer than one short clause.
    cues = []
    # Negated cut language must not become a positive direction.
    negated_cut = re.search(r"(?:不要|不必|无需|避免|不要求)[^。；;]{0,12}(?:直接切镜|硬切|DIRECT CUT)", first, re.I)
    if _HARD_CUT.search(first) and not negated_cut:
        cues.append("HARD CUT")
    # An explicitly off-mirror camera wins over a mention of an earlier
    # mirror selfie in the same sentence.
    if "镜外" in first:
        cues.append("OFF-MIRROR VIEW")
    elif "镜自拍" in first or "对镜自拍" in first:
        cues.append("MIRROR SELFIE")
    for keyword, label in _FRAMING:
        if keyword in first:
            cues.append(label)
            break
    if "横移" in first:
        cues.append("LATERAL CAMERA MOVE")
    return tuple(cues)


def _one_shot(
    index: int, start: Decimal, end: Decimal, body: str,
) -> CameraShot:
    return CameraShot(
        number=index, start=start, end=end, original_text=body,
        camera_cues=_cue_tokens(body),
        image_refs=tuple(dict.fromkeys(_IMAGE.findall(body))),
    )


def _keep(prompt: str, reason: str, shots: tuple[CameraShot, ...] = ()) -> Translation:
    checksum = _digest(prompt)
    return Translation(
        "unchanged", reason, prompt, prompt, shots, checksum, checksum,
    )


def _integer_schedule(
    shots: tuple[CameraShot, ...], duration: int,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[tuple[str, int, str], ...], str | None]:
    """Align cumulative cut boundaries; never round each shot independently.

    Rounding cut edges, not individual durations, preserves the total runtime.
    Exact half-second ties choose the later integer; all changes are audited.
    A zero-length result or failed timeline validation preserves the source.
    """
    edges = [0]
    changes: list[tuple[str, int, str]] = []
    for shot in shots[:-1]:
        cut = shot.end
        rounded = int(cut.to_integral_value(rounding=ROUND_HALF_UP))
        shift = Decimal(rounded) - cut
        if abs(shift) > _MAX_CUT_SHIFT:
            return (), (), (), "cut_shift_exceeds_0_5s"
        if shift != 0:
            changes.append((str(cut), rounded, str(shift)))
        edges.append(rounded)
    edges.append(duration)
    durations = tuple(right - left for left, right in zip(edges, edges[1:]))
    if any(seconds < 1 for seconds in durations):
        return (), (), (), "integer_shot_under_one_second"
    if sum(durations) != duration:
        return (), (), (), "integer_total_duration_mismatch"
    return tuple(edges), durations, tuple(changes), None


def translate_sol_shots(prompt: str, duration: int) -> Translation:
    """Translate Sol's fixed camera intent into an integer Kling timeline.

    Original actions, image tokens, and global constraints remain unchanged.
    Use strict native shot grammar only when no unscoped global preamble exists.
    Otherwise retain the global text ahead of a *non-native* integer timeline.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return _keep(str(prompt), "empty_or_invalid_prompt")
    if type(duration) is not int or not 3 <= duration <= 15:
        return _keep(prompt, "invalid_duration")
    try:
        headings = sorted(
            [*_NUMBERED_HEADING.finditer(prompt), *_BARE_HEADING.finditer(prompt)],
            key=lambda match: match.start(),
        )
        if not 2 <= len(headings) <= 6:
            return _keep(prompt, "not_two_to_six_recognized_shots")
        if any(a.end() > b.start() for a, b in zip(headings, headings[1:])):
            return _keep(prompt, "overlapping_shot_headers")
        # Production Sol sometimes writes 商品参考图3–5: those numerals
        # describe image IDs, NOT a 3-to-5-second cut. Ignore only such
        # explicitly prefixed image ranges; other extra time ranges still
        # force a conservative fallback.
        candidate_ranges = [
            match for match in _ANY_TIME_RANGE.finditer(prompt)
            if not re.search(
                r"(?:图片?|@image_)\s*$",
                prompt[max(0, match.start() - 12):match.start()],
            )
        ]
        if len(candidate_ranges) != len(headings):
            return _keep(prompt, "unrecognized_or_extra_time_range")
        if any(
            not any(mark.start() >= shot.start() and mark.start() < shot.end()
                    for shot in headings)
            for mark in _SHOT_MARK.finditer(prompt)
        ):
            return _keep(prompt, "unrecognized_shot_marker")
        parsed: list[CameraShot] = []
        expected = Decimal(0)
        for index, match in enumerate(headings, 1):
            label = match.groupdict().get("number") or match.groupdict().get("ordinal")
            if label:
                mapped = _CHINESE_INDEX.get(label, _CIRCLED_INDEX.get(label, label))
                if int(mapped) != index:
                    return _keep(prompt, "nonsequential_shot_numbers")
            start = _time_seconds(match.group("start"))
            end = _time_seconds(match.group("end"))
            if start != expected:
                return _keep(prompt, "timeline_gap" if start > expected else "timeline_overlap")
            if end - start < 1:
                return _keep(prompt, "shot_under_one_second")
            if end > duration:
                return _keep(prompt, "shot_exceeds_video_duration")
            stop = headings[index].start() if index < len(headings) else len(prompt)
            body = prompt[match.end():stop].strip()
            if not body:
                return _keep(prompt, "empty_shot_description")
            parsed.append(_one_shot(index, start, end, body))
            expected = end
        if expected != duration:
            return _keep(prompt, "timeline_total_mismatch")

        shots = tuple(parsed)
        global_preamble = prompt[:headings[0].start()]
        edges, durations, adjustments, schedule_error = _integer_schedule(
            shots, duration,
        )
        if schedule_error:
            return _keep(prompt, schedule_error, shots)
        original_edges = tuple(str(s.end) for s in shots[:-1])
        # A semicolon is a delimiter in native syntax, not free prose.
        # The Sol global preamble cannot silently become shot 1 instructions.
        native_safe = (
            not global_preamble.strip()
            and all(";" not in shot.original_text for shot in shots)
        )
        if native_safe:
            compiled_parts = []
            for shot in shots:
                cue = ", ".join(shot.camera_cues)
                words = (cue + ". " if cue else "") + shot.original_text
                if len(words) > _MAX_NATIVE_WORDS:
                    native_safe = False
                    break
                compiled_parts.append(
                    f"shot {shot.number}, {durations[shot.number - 1]}, {words};"
                )
            if native_safe:
                native = " ".join(compiled_parts)
                if len(native) <= _MAX_PROMPT:
                    return Translation(
                        "native_shots", "safe_integer_shot_schedule",
                        prompt, native, shots, _digest(prompt), _digest(native),
                        original_edges, edges, durations, adjustments,
                    )

        # Keep Sol global instructions outside the shots. This is non-native
        # camera language with the SAME approved integer schedule.
        # Do not misreport it as API-native parsing.
        blocks = []
        for shot in shots:
            cues = ", ".join(shot.camera_cues)
            suffix = f" | {cues}" if cues else ""
            blocks.append(
                f"SHOT {shot.number} [{edges[shot.number - 1]}–{edges[shot.number]}s]{suffix}\n"
                f"{shot.original_text}"
            )
        rendered = global_preamble.strip()
        rendered = (rendered + "\n\n" if rendered else "") + "\n\n".join(blocks)
        if len(rendered) > _MAX_PROMPT:
            return _keep(prompt, "translated_prompt_over_3072", shots)
        reason = (
            "global_preamble_kept_outside_native_shots"
            if global_preamble.strip() else
            "native_grammar_guard_fallback"
        )
        return Translation(
            "camera_language", reason, prompt, rendered,
            shots, _digest(prompt), _digest(rendered),
            original_edges, edges, durations, adjustments,
        )
    except (InvalidOperation, ValueError, OverflowError, AttributeError):
        return _keep(prompt, "unparseable_timeline")
