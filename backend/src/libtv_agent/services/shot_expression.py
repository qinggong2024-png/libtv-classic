"""Select at most one small facial change after allocating the shot's motion."""

from libtv_agent.templates.facial_expressions import (
    EXPRESSION_ALIASES,
    EXPRESSION_LIBRARY,
    FaceEvidence,
)
from libtv_agent.templates.motion_actions import ActionCapability, ActionContext


def plan_shot_expression(
    *,
    context: ActionContext,
    face: FaceEvidence,
    pool: tuple[str, ...],
    duration: float,
    remaining: float,
    variant_index: int,
    occupied_by_motion: bool,
) -> tuple[str, dict[str, object]]:
    metadata: dict[str, object] = {
        "selectedExpressionId": None,
        "expressionChange": False,
        "sourceMouthState": face.mouth,
        "naturalBlinkAllowed": False,
    }
    if not context.supports((ActionCapability.FACE_VISIBLE,)):
        return "", {**metadata, "expressionReason": "face_not_visible"}
    preserve = "保留参考图原有神态、嘴角和口型，不强行改成笑脸或冷脸"
    blink = face.eyes_visible and duration >= 2
    if blink:
        preserve += "，允许自然眨眼，不规定次数或刻意眨单眼"
    metadata.update(selectedExpressionId="preserve_source", naturalBlinkAllowed=blink)
    reason = "source_uncertain"
    if duration < 2:
        reason = "shot_too_short"
    elif occupied_by_motion:
        reason = "coordinated_with_motion"
    elif not face.gentle_expression:
        reason = "source_expressive" if face.mouth == "open_or_expressive" else "source_uncertain"
    elif face.mouth == "soft_smile":
        reason = "preserve_existing_smile"
    else:
        candidates = []
        for text in pool:
            expression = EXPRESSION_LIBRARY.get(EXPRESSION_ALIASES.get(text, text))
            if expression is None or expression in candidates:
                continue
            if expression.changes_mouth and (
                remaining < 2 or not context.supports((ActionCapability.FACE_GAZE_ADJUSTABLE,))
            ):
                continue
            candidates.append(expression)
        if candidates:
            selected = candidates[(variant_index - 1) % len(candidates)]
            instruction = selected.instruction
            if blink:
                instruction += "；允许自然眨眼，不规定次数或刻意眨单眼"
            return instruction, {
                **metadata,
                "selectedExpressionId": selected.id,
                "expressionChange": selected.changes_mouth,
                "expressionReason": "source_compatible_brand_expression",
            }
        reason = "no_compatible_expression"
    return preserve, {**metadata, "expressionReason": reason}
