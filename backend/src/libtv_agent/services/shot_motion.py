"""Compile motion against one storyboard's confirmed facts at a time."""

from collections.abc import Mapping
from dataclasses import replace

from libtv_agent.services.shot_expression import plan_shot_expression
from libtv_agent.templates.facial_expressions import UNKNOWN_FACE, FaceEvidence
from libtv_agent.templates.motion_actions import (
    ACTION_LIBRARY,
    ActionCapability,
    ActionContext,
    MotionAction,
    action_family,
    action_min_seconds,
    compatible_action_ids,
    render_action_ids,
    select_brand_motion,
)
from libtv_agent.templates.site_style_profiles import SiteStyleProfile
from libtv_agent.templates.video_templates import get_video_template

_CONTRADICTORY_CAPABILITIES = {
    ActionCapability.STANDING_POSE: frozenset({ActionCapability.SEATED_POSE}),
    ActionCapability.SEATED_POSE: frozenset({ActionCapability.STANDING_POSE}),
}
_BEAT_WEIGHTS: dict[int, tuple[float, ...]] = {
    1: (1.0,),
    2: (0.55, 0.45),
    3: (0.40, 0.30, 0.30),
    4: (0.34, 0.23, 0.23, 0.20),
}
def _explicitly_incompatible(action: MotionAction, context: ActionContext) -> bool:
    return any(
        capability in context.denied
        or bool(_CONTRADICTORY_CAPABILITIES.get(capability, frozenset()) & context.confirmed)
        for capability in action.required_capabilities
    )


def _ordered_unique(action_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(action_id for action_id in action_ids if action_id))


def _interleave_unique(
    primary: tuple[str, ...],
    secondary: tuple[str, ...],
) -> tuple[str, ...]:
    ordered: list[str] = []
    for index in range(max(len(primary), len(secondary))):
        if index < len(primary):
            ordered.append(primary[index])
        if index < len(secondary):
            ordered.append(secondary[index])
    return _ordered_unique(tuple(ordered))


def _motion_beats(
    action_ids: list[str],
    duration: float,
    actions: Mapping[str, MotionAction],
) -> list[dict[str, object]]:
    """Allocate the full shot duration across a short visible action sequence."""

    weights = _BEAT_WEIGHTS.get(len(action_ids), ())
    durations = [round(duration * weight, 2) for weight in weights]
    if durations:
        durations[-1] = round(duration - sum(durations[:-1]), 2)
    beats: list[dict[str, object]] = []
    elapsed = 0.0
    for action_id, action_duration in zip(action_ids, durations, strict=True):
        end = min(duration, elapsed + action_duration)
        beats.append(
            {
                "actionId": action_id,
                "startSeconds": round(elapsed, 2),
                "endSeconds": round(end, 2),
            }
        )
        elapsed = end
    return beats


def _target_beat_count(duration: float) -> int:
    if duration < 6:
        return 1
    if duration <= 8:
        return 2
    if duration <= 12:
        return 3
    return 4


def _render_motion_beats(
    *,
    template_id: str,
    beats: list[dict[str, object]],
    context: ActionContext,
    library: Mapping[str, MotionAction],
) -> str:
    labels = ("前段", "后段") if len(beats) == 2 else ("前段", "中段", "后段")
    rendered: list[str] = []
    for index, beat in enumerate(beats):
        action_id = str(beat["actionId"])
        duration = float(beat["endSeconds"]) - float(beat["startSeconds"])
        label = labels[index] if index < len(labels) else f"第{index + 1}段"
        instruction = render_action_ids(
            template_id,
            (action_id,),
            context=context,
            library=library,
        ).rstrip("。；")
        rendered.append(f"{label}（约{duration:g}秒）：{instruction}")
    return "；".join(rendered)


def plan_shot_motion(
    *,
    profile: SiteStyleProfile,
    template_id: str,
    context: ActionContext,
    duration: float,
    variant_index: int,
    library: Mapping[str, MotionAction] | None,
    preferred: tuple[str, ...] = (),
    avoided: tuple[str, ...] = (),
    recent_actions: tuple[str, ...] = (),
    recent_combos: tuple[str, ...] = (),
    face_evidence: FaceEvidence = UNKNOWN_FACE,
    template_motion_pool: tuple[str, ...] | None = None,
    template_detail_pool: tuple[str, ...] | None = None,
    selection_salt: int = 0,
) -> tuple[str, dict[str, object]]:
    actions = ACTION_LIBRARY if library is None else library
    # A catalog override may customize wording, but cannot erase the shipped
    # starting conditions or inherited general-action requirements.
    safe_actions = {
        key: replace(
            action,
            required_capabilities=tuple(
                dict.fromkeys(
                    (
                        *action.required_capabilities,
                        *(
                            actions[action.base_action_id].required_capabilities
                            if action.base_action_id in actions
                            else ()
                        ),
                        *(
                            ACTION_LIBRARY[action.base_action_id].required_capabilities
                            if action.base_action_id in ACTION_LIBRARY
                            else ()
                        ),
                        *(
                            ACTION_LIBRARY[key].required_capabilities
                            if key in ACTION_LIBRARY
                            else ()
                        ),
                    )
                )
            ),
        )
        for key, action in actions.items()
        if action_min_seconds(action) <= duration
        and (
            "eye_contact_soft_smile" not in (key, action.base_action_id)
            or face_evidence.gentle_expression
        )
    }
    for key, action in tuple(safe_actions.items()):
        if "eye_contact_soft_smile" not in (key, action.base_action_id):
            continue
        if action.base_action_id == "eye_contact_soft_smile":
            instruction = "沿用基础动作的神态，不追加嘴部或视线变化。"
        elif face_evidence.mouth == "soft_smile":
            instruction = "保持参考图已有的轻微微笑与原有视线，自然停留；不重新从中性变笑，不露齿。"
        else:
            instruction = (
                "沿用参考图原有视线，嘴角轻微上扬一次并自然停留；"
                "不露齿、不张嘴或做说话口型，身体与服装保持稳定。"
            )
        safe_actions[key] = replace(action, instruction=instruction)
    template = get_video_template(template_id)
    profile_pool = profile.primary_motion_pool or profile.preferred_actions
    configured_detail_pool = (
        template.detail_motion_pool
        if template_detail_pool is None
        else template_detail_pool
    )
    detail_ids = set(configured_detail_pool)
    brand_primary_pool = tuple(
        action_id
        for action_id in profile_pool
        if action_id not in detail_ids
        and action_id in actions
    )
    template_primary_pool = (
        template.motion_action_pool
        if template_motion_pool is None
        else template_motion_pool
    )
    selected_primary_order = (
        _interleave_unique(template_primary_pool, brand_primary_pool)
        if template_primary_pool
        else _ordered_unique(profile_pool)
    )
    preferred_primary = tuple(
        action_id for action_id in preferred if action_id in selected_primary_order
    )
    primary_pool = _ordered_unique((*preferred_primary, *selected_primary_order))
    # The user-selected template authorizes its creative direction. Treat
    # unobserved capabilities as available for low-risk template-compatible
    # primary motions, while preserving explicit denials as hard constraints.
    # Face handling below deliberately keeps using the observed context.
    creative_context = ActionContext(
        confirmed=frozenset(ActionCapability) - context.denied,
        denied=context.denied,
    )
    detail_pool = _ordered_unique(
        (
            *(action_id for action_id in preferred if action_id in detail_ids),
            *configured_detail_pool,
            *(
                action_id
                for action_id in profile_pool
                if action_id in actions and actions[action_id].category == "detail"
            ),
        )
    )
    compatible_details = tuple(
        action_id
        for action_id in detail_pool
        if action_id in actions
        and action_id not in avoided
        and not _explicitly_incompatible(actions[action_id], context)
        and action_min_seconds(actions[action_id]) <= duration
    )
    compatible_primary = tuple(
        action_id
        for action_id in primary_pool
        if action_id not in avoided
        and action_id in actions
        and not _explicitly_incompatible(actions[action_id], context)
    )
    if compatible_primary:
        primary_pool = compatible_primary

    selection = select_brand_motion(
        template_id=template_id,
        primary_motion_pool=primary_pool,
        micro_motion_pool=(),
        expression_pool=(),
        camera_pool=(),
        variant_index=variant_index + selection_salt,
        context=creative_context,
        library=safe_actions,
        recent_selected_action_ids=recent_actions,
        recent_motion_combinations=recent_combos,
        dedupe_recent_count=profile.dedupe_recent_count,
        dedupe_combo_window=profile.dedupe_combo_window,
        dedupe_family_window=profile.dedupe_family_window,
        dedupe_family_limit=profile.dedupe_family_limit,
    )
    primary_id = selection.primary_action_id or "breath_settle"
    selected = [primary_id]
    primary = safe_actions.get(primary_id, ACTION_LIBRARY["breath_settle"])
    remaining = duration - action_min_seconds(primary)
    primary_family = action_family(primary.base_action_id or primary_id, library=actions)
    primary_owns_face = primary_family in {
        "walking", "turning", "back_reveal", "gaze_reaction",
    } or (primary.base_action_id or primary_id) in {"head_shoulder_tilt", "companion_gaze_return"}
    expression, expression_metadata = plan_shot_expression(
        context=context, face=face_evidence, pool=profile.expression_pool,
        duration=duration, remaining=remaining, variant_index=variant_index,
        occupied_by_motion=primary_owns_face,
    )
    # Add movement/pose and product/detail beats until the duration's target
    # count is reached: 8s -> 2 beats, 10s -> 3, 15s -> 4.
    support_candidates = tuple(
        action_id
        for action_id in _interleave_unique(template_primary_pool, brand_primary_pool)
        if action_id in safe_actions
        and action_id not in selected
        and action_id not in avoided
        and not _explicitly_incompatible(safe_actions[action_id], context)
    )
    primary_family_name = action_family(primary_id, library=actions)
    support_motion_pool = tuple(
        action_id
        for action_id in support_candidates
        if action_family(action_id, library=actions) != primary_family_name
    ) + tuple(
        action_id
        for action_id in support_candidates
        if action_family(action_id, library=actions) == primary_family_name
    )
    target_beats = _target_beat_count(duration)
    movement_families = {"walking", "turning", "standing_attitude"}
    requires_movement = template_id == "ootd_walk" and target_beats >= 2
    configured_action_pool = _ordered_unique(
        (
            *selected_primary_order,
            *detail_pool,
        )
    )
    fallback_candidates = tuple(
        action_id
        for action_id in compatible_action_ids(
            template_id,
            configured_action_pool,
            context=creative_context,
            library=safe_actions,
        )
        if action_id in safe_actions
        and not _explicitly_incompatible(safe_actions[action_id], context)
        and action_id != "breath_settle"
        and action_id not in selected
        and action_id not in avoided
    )
    detail_history = list(recent_actions)
    detail_combo_history = list(recent_combos)
    for beat_index in range(1, target_beats):
        has_movement = any(
            action_family(action_id, library=actions) in movement_families
            for action_id in selected
        )
        movement_slots = (
            requires_movement
            and not has_movement
            or target_beats >= 3
            and beat_index == 1
        )
        movement_candidates = tuple(
            action_id
            for action_id in support_motion_pool
            if action_id not in selected
            and action_family(action_id, library=actions) in movement_families
        )
        fitting_details = tuple(
            action_id
            for action_id in compatible_details
            if action_id not in selected
            and action_id in safe_actions
        )
        if requires_movement and not has_movement:
            candidate_pool = movement_candidates
        elif movement_slots:
            candidate_pool = (*movement_candidates, *fitting_details)
        else:
            candidate_pool = fitting_details
        candidate_pool = tuple(dict.fromkeys(candidate_pool))
        if not candidate_pool:
            candidate_pool = tuple(
                action_id
                for action_id in fallback_candidates
                if action_id not in selected
            )
        if not candidate_pool or not selection.primary_action_id:
            break
        next_selection = select_brand_motion(
            template_id=template_id,
            primary_motion_pool=candidate_pool,
            variant_index=variant_index + selection_salt,
            context=creative_context,
            library=safe_actions,
            recent_selected_action_ids=tuple(detail_history),
            recent_motion_combinations=tuple(detail_combo_history),
            dedupe_recent_count=profile.dedupe_recent_count,
            dedupe_combo_window=profile.dedupe_combo_window,
            dedupe_family_window=profile.dedupe_family_window,
            dedupe_family_limit=profile.dedupe_family_limit,
        )
        if not next_selection.primary_action_id:
            break
        next_action_id = next_selection.primary_action_id
        selected.append(next_action_id)
        detail_history.insert(0, next_action_id)
        detail_combo_history.insert(0, next_selection.combo_key or f"{next_action_id}::default")

    for action_id in fallback_candidates:
        if len(selected) >= target_beats:
            break
        if action_id not in selected:
            selected.append(action_id)

    # Profiles without a template-owned detail beat can still add one low-risk
    # expression micro action when the face evidence explicitly supports it.
    if not compatible_details and (
        remaining >= 2
        and selection.primary_action_id
        and not expression_metadata["expressionChange"]
        and action_family(primary_id, library=actions)
        not in {
            "walking",
            "turning",
            "back_reveal",
            "gaze_reaction",
        }
        and ActionCapability.BACK_REFERENCE not in (primary.required_capabilities)
    ):
        micro_library = {
            key: action
            for key, action in safe_actions.items()
            if key != primary_id
            and (
                key in {"controlled_gaze", "eye_contact_soft_smile"}
                or action.base_action_id in {"controlled_gaze", "eye_contact_soft_smile"}
            )
            and key not in avoided
            and action_min_seconds(action) <= remaining
            and action_family(key, library=actions) != action_family(primary_id, library=actions)
            and ActionCapability.FREE_HAND not in action.required_capabilities
            and context.supports(
                tuple(
                    dict.fromkeys((*primary.required_capabilities, *action.required_capabilities))
                )
            )
        }
        micro = select_brand_motion(
            template_id=template_id,
            primary_motion_pool=profile.micro_motion_pool,
            variant_index=variant_index,
            context=context,
            library=micro_library,
        ).primary_action_id
        if micro:
            selected.append(micro)
    render_library = {**safe_actions, "breath_settle": ACTION_LIBRARY["breath_settle"]}
    beats = _motion_beats(selected, duration, render_library)
    instruction = _render_motion_beats(
        template_id=template_id,
        beats=beats,
        context=creative_context,
        library=render_library,
    )
    if len(selected) > 1:
        final_beat_duration = float(beats[-1]["endSeconds"]) - float(
            beats[-1]["startSeconds"]
        )
        expression, expression_metadata = plan_shot_expression(
            context=context, face=face_evidence, pool=profile.expression_pool,
            duration=duration, remaining=final_beat_duration, variant_index=variant_index,
            occupied_by_motion=False,
        )
    if face_evidence.mouth == "neutral" and any(
        "eye_contact_soft_smile" in (key, safe_actions[key].base_action_id)
        for key in selected
        if key in safe_actions
    ):
        expression = "沿用动作中的一次轻微笑意，不叠加其他表情变化"
        if expression_metadata["naturalBlinkAllowed"]:
            expression += "；允许自然眨眼，不规定次数或刻意眨单眼"
        expression_metadata.update(
            selectedExpressionId="warm_half_smile",
            expressionChange=True,
            expressionReason="coordinated_with_smile_action",
        )
    camera = "镜头跟随动作推进，可使用跟随、推进、侧移或三分之四角度变化"
    if ActionCapability.FIXED_CAMERA in context.confirmed:
        camera = "保持固定机位，不推拉、不跟随、不添加手持晃动"
    elif ActionCapability.MIRROR_SELFIE in context.confirmed:
        camera = "保持镜自拍持机手与镜面关系，不切换为第三人拍摄"
    parts = [instruction, f"摄影：{camera}"]
    if expression:
        parts.append(f"表情：{expression}")
    return "；".join(parts), {
        "selectedActionIds": selected,
        "selectedPrimaryActionId": primary_id,
        "selectedMotionCombination": "::".join(selected),
        "selectedMicroActionId": selected[1] if len(selected) > 1 else None,
        **expression_metadata,
        "fallback": selection.primary_action_id is None,
        "confirmed": sorted(item.value for item in context.confirmed),
        "denied": sorted(item.value for item in context.denied),
        "motionBeats": beats,
    }
