"""Opt-in action-first director for Kling 3.0 Omni, 5–15-second real UGC.

Sol provides the actual usable actions and cut instructions as TEXT to the CLI.
Never turn a suggestive editing rhythm into fake provider-side timing controls.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from libtv_agent.templates.garment_materials import GarmentEdgeFinish

logger = logging.getLogger(__name__)

PROFILE = "allwe-kling-v34-natural-first-v1"
CUT_MARKER = re.compile(r"(?<!\w)(?:HARD\s+)?CUT\s+TO\b", re.IGNORECASE)

# This replaces the legacy shot-policy prompt for V3 only.
ACTION_SYSTEM_PROMPT = (
    "You direct real, watchable 5-15 second handheld-phone UGC for Kling. "
    "WRITE THE VIDEO_PROMPT FIRST as simple natural filming instructions, "
    "not a checklist of tiny movements or a storyboard table. Study the "
    "storyboard image for the existing wearer, starting pose, light, setting "
    "and available space; use product images as the sole truth for exact garment "
    "artwork, spelling, colors, material, sleeves, neckline and fit. "
    "Choose a filmable visual idea specifically suited to THIS garment, then "
    "describe how a person naturally acts, reacts and shows it in one coherent "
    "little sequence. The original printed joke can be the entire hook: a "
    "witty glance or deadpan confidence often works better than acting out "
    "a fabricated story. Freely choose natural eye movement, expressions, "
    "gestures, garment touches, short turns, camera approaches and useful "
    "new angles; no mandatory walking, pointing, grinning, plot twist or "
    "fixed shot order. Move at normal everyday speed with living expressions, "
    "not slow-motion posing. Keep enough visible development through the "
    "whole clip: on a 10-second video, do not spend half the film holding "
    "one unchanging shot; allow clear, prompt CUT TO transitions to distinct "
    "wide/medium/close viewpoints when they actually add something. "
    "Start naturally from the supplied image; keep the same real person, "
    "existing room or outdoor setting and props. No invented people, extra "
    "objects, new locations, fabricated product claims, altered garment text "
    "or graphic details. Use believable skin and fabric, simple imperfect "
    "phone camerawork. Silent footage: no dialogue, music or added captions. "
    "Write video_prompt as the complete concise English Kling instruction, "
    "usually a few fluent sentences; the compiler adds image reference roles. "
    "ONLY AFTER the creative prompt is complete, fill the required visual "
    "observation fields and shot rows to DESCRIBE what the prompt already "
    "directs. Treat shot roles, durations and metadata as bookkeeping, "
    "not creative requirements. Each cut row must correspond to a real "
    "CUT TO in video_prompt, and one uninterrupted camera move needs no "
    "extra artificial cut."
)

NATURAL_WIRE_DIRECTIVE = (
    "V3.4 NATIVE WIRE: video_prompt is the creative source of truth. "
    "Write the natural-language sequence first, then reflect it in compact "
    "shots metadata and source-image observations. There is no prescribed "
    "hook/reveal/proof/payoff order, fixed camera ladder or action quota. "
    "Each actual CUT TO becomes one shot transition; gestures within a "
    "shot do not need separate rows. Use frame/person/prop references only "
    "when supported by visible evidence. Preserve the original artwork "
    "and any text exactly; never fabricate selling claims. Silent video."
)

DIRECTIVE = (
    "NATURAL VIDEO DIRECTION: give Kling concrete natural full-speed "
    "actions, evolving expressions, and responsive handheld camerawork. "
    "Let the actual image and garment suggest what happens; a readable "
    "print, useful angle change, casual garment demonstration or subtle "
    "visual joke is enough. For longer clips include more distinct useful "
    "visual moments without padding or choreographed walking. Write "
    "the final video_prompt in plain English first, with decisive CUT TO "
    "only for motivated viewpoint changes; then summarize it in wire fields."
)


def max_shots_for_duration(duration: int) -> int:
    if not 5 <= duration <= 15:
        raise ValueError("V3 action director requires 5–15 seconds")
    if duration <= 6:
        return 3
    if duration <= 8:
        return 4
    if duration <= 11:
        return 5
    return 6


def native_v34_eligible(
    *,
    enabled: bool,
    generation_mode: str,
    duration: int,
    cut_mode: str,
    provider: str,
    model: str,
    ai_model: str,
) -> bool:
    return bool(
        enabled
        and generation_mode == "automatic"
        and 5 <= duration <= 15
        and cut_mode == "explicit_cuts"
        and provider == "cli"
        and model == "kling-video-v3_0_omni"
        and ai_model.strip().casefold() in {"gpt-6.1-sol", "gpt-6.2-sol"}
    )


def creative_guidance(duration: int) -> str:
    """Duration and useful visual rhythm, without prewriting shot types."""
    cap = max_shots_for_duration(duration)
    return (
        f"Make a natural {duration}s video with up to {cap} useful views. "
        "Write an engaging, filmable natural-language sequence FIRST. "
        "Use a few purposeful actions and decisive camera changes that "
        "actually reveal this garment and its personality; longer videos "
        "can have more visual moments but must never stretch a gesture "
        "or hold an unchanged pose to fill time. The camera can move "
        "instead of the person. A CUT TO is useful when it reveals "
        "new visual information, not just to satisfy a shot count. "
        "Keep the original garment and location exact, real everyday speed, "
        "visible expressions and smooth action continuity."
    )

def _fit_cut_windows(windows: Sequence[float], segments: int) -> list[float]:
    """Redistribute the planned edit windows onto the authored cut count.

    The planner's beat count and the writer's CUT TO count drift apart easily,
    and anchoring only on an exact match made the whole time anchor disappear
    without a trace. Resample instead: total duration is preserved and the
    planned rhythm is kept as far as the real segment count allows.
    """
    weights = [float(weight) for weight in windows]
    total = sum(weights)
    if segments <= 0 or total <= 0:
        return []
    if segments == len(weights):
        return weights
    bounds = [0.0]
    running = 0.0
    for weight in weights:
        running += weight
        bounds.append(running / total)
    fitted: list[float] = []
    for slot in range(segments):
        start, end = slot / segments, (slot + 1) / segments
        mass = 0.0
        for index, weight in enumerate(weights):
            low, high = bounds[index], bounds[index + 1]
            if high <= low:
                continue
            overlap = min(high, end) - max(low, start)
            if overlap > 0:
                mass += weight * overlap / (high - low)
        fitted.append(mass)
    return fitted


def _annotate_cut_times(creative: str, windows: list[float]) -> str:
    """Attach editorial cut times to the cuts the writer already authored.

    Kling 3.0 Omni exposes no provider-side per-shot duration field, so CUT TO
    without a time anchor reads as a weak suggestion and the model tends to hold
    one framing for the whole clip. These are plain prompt-text anchors: they
    never add, remove or reorder a cut, and they leave the creative wording
    untouched.

    The planner's shot count and the writer's cut count are independent, so the
    planned windows are realigned onto whatever cuts actually exist. A silent
    no-op here would disable the only working lever without any signal.
    """
    cuts = list(CUT_MARKER.finditer(creative))
    if not cuts:
        logger.warning(
            "native-v34 time anchors skipped: video_prompt carries no CUT TO, "
            "so Kling receives no shot boundary; profile=%s plannedShots=%d",
            PROFILE, len(windows),
        )
        return creative
    planned_cuts = len(windows) - 1
    if len(cuts) != planned_cuts:
        logger.warning(
            "native-v34 time anchors realigned: authoredCuts=%d plannedCuts=%d "
            "plannedShots=%d; planned rhythm resampled onto the authored cuts",
            len(cuts), planned_cuts, len(windows),
        )
    fitted = _fit_cut_windows(windows, len(cuts) + 1)
    if not fitted or any(weight <= 0 for weight in fitted):
        logger.warning(
            "native-v34 time anchors skipped: unusable shot windows; "
            "authoredCuts=%d plannedShots=%d", len(cuts), len(windows),
        )
        return creative
    pieces: list[str] = []
    cursor = 0
    elapsed = 0.0
    for index, match in enumerate(cuts):
        elapsed += fitted[index]
        pieces.append(creative[cursor : match.start()])
        pieces.append(f"at {round(elapsed, 1):g}s {match.group(0)}")
        cursor = match.end()
    pieces.append(creative[cursor:])
    body = "".join(pieces).rstrip()
    marks: list[str] = []
    elapsed = 0.0
    for weight in fitted[:-1]:
        elapsed += weight
        marks.append(f"{round(elapsed, 1):g}s")
    longest = round(max(fitted), 1)
    return (
        f"{body} Cuts land at {', '.join(marks)}; no single framing holds "
        f"longer than {longest:g} seconds."
    )


def compile_native_v34_prompt(
    text: str,
    *,
    storyboard_count: int,
    product_count: int,
    duration: int,
    shot_count: int,
    cuff_finish: GarmentEdgeFinish,
    hem_finish: GarmentEdgeFinish,
    shot_windows: Sequence[float] | None = None,
    enforce_content_review: bool = True,
) -> str:
    """Safety/transport checks only; do not ban organic movement or invent cuts."""
    from libtv_agent.services.ai_creative_planner import CreativePlannerError

    cap = max_shots_for_duration(duration)
    if enforce_content_review and not 2 <= shot_count <= cap:
        raise CreativePlannerError("V3 action shot count exceeds duration capacity")
    creative = text.strip()
    if enforce_content_review and not 80 <= len(creative) <= 1600:
        raise CreativePlannerError("V3 action prompt length invalid")
    cuts = list(CUT_MARKER.finditer(creative))
    if enforce_content_review and len(cuts) != shot_count - 1:
        raise CreativePlannerError("V3 action prompt cuts do not match planned views")
    if enforce_content_review and not creative.endswith((".", "!", "?")):
        raise CreativePlannerError("V3 action prompt ends incompletely")
    if enforce_content_review and re.search(
        r"\b(?:duration_weight|frame-local actors|json schema)\b", creative, re.I
    ):
        raise CreativePlannerError("V3 action prompt includes wire metadata")
    if enforce_content_review and re.search(r"\bvoiceover\b", creative, re.I):
        for marker in re.finditer(r"\bvoiceover\b", creative, re.I):
            clause = re.split(r"[.!?;]", creative[max(0, marker.start() - 150) : marker.start()])[
                -1
            ]
            if not re.search(r"\b(?:no|without|never)\b", clause, re.I):
                raise CreativePlannerError("V3 action prompt requests voiceover")
    if shot_windows is not None:
        try:
            windows = [float(weight) for weight in shot_windows]
        except (TypeError, ValueError):
            windows = []
        if len(windows) == shot_count and all(weight > 0 for weight in windows):
            creative = _annotate_cut_times(creative, windows)
    references = storyboard_count + product_count
    for ref in re.findall(r"@image_(\d+)\b", creative, re.I):
        if enforce_content_review and not 1 <= int(ref) <= references:
            raise CreativePlannerError("V3 action prompt uses unbound reference")
    actors = ", ".join(f"@image_{i}" for i in range(1, storyboard_count + 1))
    if product_count:
        garments = ", ".join(f"@image_{i}" for i in range(storyboard_count + 1, references + 1))
        role = (
            f"Use {actors} for the same real wearer and place; {garments} for the "
            "exact garment design, print and lettering only."
        )
    else:
        role = f"Use {actors} for the real wearer, location and exact original garment."
    edges = []
    if cuff_finish == "non_ribbed":
        edges.append("cuffs")
    if hem_finish == "non_ribbed":
        edges.append("hem")
    if edges:
        role += f" Keep {' and '.join(edges)} as pictured, without added ribbing."
    final = (
        f"{role}\n{duration}s vertical handheld phone UGC, natural full-speed motion. {creative}"
    )
    if len(final) > 3072:
        raise CreativePlannerError("V3 action prompt exceeds Kling limit")
    return final
