from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from libtv_agent.services.shot_motion import plan_shot_motion
from libtv_agent.templates.facial_expressions import FaceEvidence
from libtv_agent.templates.garment_materials import (
    GarmentMaterial,
    garment_material_guidance,
)
from libtv_agent.templates.motion_actions import (
    ACTION_LIBRARY,
    ActionContext,
    MotionAction,
    canonical_action_id,
    compatible_action_ids,
    normalize_action_ids,
    render_action_ids,
    select_brand_motion,
)
from libtv_agent.templates.site_style_profiles import SiteStyleProfile
from libtv_agent.templates.video_templates import (
    ActionIntensity,
    CameraTempo,
    CutMode,
    VideoTemplate,
)


@dataclass(frozen=True)
class PromptCompileInput:
    template: VideoTemplate
    storyboard_count: int
    result_count: int
    action_intensity: ActionIntensity
    camera_tempo: CameraTempo
    cut_mode: CutMode
    variant_index: int
    duration_seconds: int | None = None
    site_profile: SiteStyleProfile | None = None
    action_context: ActionContext | None = None
    action_library: Mapping[str, MotionAction] | None = None
    learning_guidance: str = ""
    creative_brief: str = ""
    learning_preferred_action_ids: tuple[str, ...] = ()
    learning_avoided_action_ids: tuple[str, ...] = ()
    learning_profile_version: int | None = None
    learning_evidence_count: int = 0
    recent_selected_action_ids: tuple[str, ...] = ()
    recent_motion_combinations: tuple[str, ...] = ()
    prompt_budget: int = 3072
    shot_action_contexts: tuple[ActionContext, ...] = ()
    shot_evidence_sources: tuple[str, ...] = ()
    shot_face_evidence: tuple[FaceEvidence, ...] = ()
    garment_material: GarmentMaterial = "unknown"
    selection_salt: int = 0


@dataclass(frozen=True)
class ShotInstruction:
    order: int
    start_seconds: float
    end_seconds: float
    instruction: str


@dataclass(frozen=True)
class CompiledVideoPlan:
    template_id: str
    template_version: str
    master_prompt: str
    shot_instructions: tuple[ShotInstruction, ...]
    invariants: tuple[str, ...]
    negative_prompt: str
    variant_seed: dict[str, object]
    site_profile_id: str | None = None
    site_profile_version: str | None = None


_MAX_KLING_PROMPT_CHARS = 3072
_NEGATIVE_PROFILE_PREFIXES = (
    "避免",
    "不得",
    "不要",
    "不用",
    "不使用",
    "不做",
    "不呈现",
    "不新增",
    "不凭空",
    "不能",
    "无意义",
)

_VARIANT_MODIFIERS: tuple[tuple[str, str, str], ...] = (
    (
        "模板主动作完整执行一次，结束时回到对应输入分镜支持的稳定姿态",
        "保持站点表演基调，视线稳定",
        "自然呼吸感",
    ),
    (
        "把模板主动作拆成两个克制的小幅阶段，结束时保持对应输入分镜支持的稳定姿态",
        "在站点表演基调内更克制，短暂离镜后回看",
        "稳定lookbook构图",
    ),
    (
        "模板主动作中加入一次短暂停顿，结束时放松定姿",
        "在站点表演基调内增加一次自然表情变化",
        "近距离朋友视角",
    ),
    (
        "模板主动作完成后回到起始稳定姿态，结束时克制定姿",
        "在站点表演基调内更冷静，视线坚定",
        "简洁克制的编辑感",
    ),
)

_PACING_LABELS: tuple[tuple[str, str], ...] = (
    ("standard_to_slightly_fast", "节奏稍快但保持自然"),
    ("standard_to_fast", "节奏明快、衔接紧凑"),
    ("slow_to_standard", "由舒缓自然过渡到标准节奏"),
    ("standard_to_slow", "由标准自然放慢"),
    ("standard", "节奏均衡"),
    ("fast", "节奏明快"),
    ("slow", "节奏舒缓"),
)
_AMPLITUDE_GUIDANCE: dict[str, str] = {
    "low": "动作完整清楚，保留自然的重心与肩身变化",
    "standard": "动作有明确重心变化和身体张力，允许适度夸张",
    "high": "动作幅度和节奏更鲜明，允许表现性夸张但保持身体可信",
}


def _humanize_pacing(value: str) -> str:
    rendered = value
    for token, label in _PACING_LABELS:
        rendered = rendered.replace(token, label)
    return rendered


def _cut_instruction(cut_mode: CutMode) -> str:
    if cut_mode == "explicit_cuts":
        return "按分镜顺序组织清楚、有节奏的切镜"
    return "优先使用连续运动，需要时用干净切镜衔接"


def _shot_rule(template: VideoTemplate, order: int, total: int) -> str:
    rules = template.prompt_rules
    if total == 1:
        selected = (*rules.opening, *rules.middle, *rules.ending)
    elif order == 1:
        selected = rules.opening
    elif order == total:
        selected = rules.ending
    else:
        selected = rules.middle
    rendered = "；".join(selected)
    if total == 1:
        return rendered.replace("按分镜顺序", "在输入支持范围内")
    return rendered


def _ootd_ugc_action_ids(
    value: PromptCompileInput,
    shot_motion: list[tuple[str, dict[str, object]]],
    selected_action_ids: tuple[str, ...],
) -> tuple[str, str]:
    library = value.action_library or ACTION_LIBRARY
    detail_ids = {canonical_action_id(action_id) for action_id in value.template.detail_motion_pool}
    unavailable = set(value.learning_avoided_action_ids)
    action_groups = [
        tuple(str(action_id) for action_id in metadata.get("selectedActionIds", ()))
        for _, metadata in shot_motion
    ]
    if not action_groups:
        action_groups = [normalize_action_ids(selected_action_ids)]

    def usable(action_ids: tuple[str, ...], *, reverse: bool = False) -> str | None:
        ordered = reversed(action_ids) if reverse else action_ids
        for action_id in ordered:
            canonical_id = canonical_action_id(action_id)
            if (
                canonical_id not in detail_ids
                and canonical_id not in unavailable
                and canonical_id in library
            ):
                return canonical_id
        return None

    first_id = usable(action_groups[0])
    last_id = usable(action_groups[-1], reverse=True)
    fallback_ids = normalize_action_ids(
        (
            *value.learning_preferred_action_ids,
            *value.template.motion_action_pool,
            *(
                value.site_profile.primary_motion_pool
                if value.site_profile is not None
                else ()
            ),
        )
    )
    fallback = [action_id for action_id in fallback_ids if usable((action_id,))]
    if first_id is None and fallback:
        first_id = fallback[(value.variant_index - 1) % len(fallback)]
    if last_id is None or last_id == first_id:
        alternatives = [action_id for action_id in fallback if action_id != first_id]
        if alternatives:
            last_id = alternatives[value.variant_index % len(alternatives)]
    return (
        first_id or "stance_shift",
        last_id or first_id or "three_quarter_turn",
    )


def _render_ootd_ugc_prompt(
    *,
    material: GarmentMaterial,
    first_action_id: str,
    last_action_id: str,
    creative_brief: str,
    library: Mapping[str, MotionAction],
) -> str:
    first_label = library.get(first_action_id, ACTION_LIBRARY["stance_shift"]).label
    last_label = library.get(last_action_id, ACTION_LIBRARY["three_quarter_turn"]).label
    material_text = garment_material_guidance(material).rstrip("。； ")
    creative_text = (
        f"\n创意要求：{' '.join(creative_brief.split()).strip()}。"
        if creative_brief.strip()
        else ""
    )
    return (
        "生成一段动态视频，智能分镜。iPhone17后置拍摄效果模仿手持镜头拍摄，"
        "画面带有轻微自然的晃动。模特做了几个自然的动作展示今天的穿搭，"
        f"{material_text}。画面整体呈现真实自然，模特皮肤不要过度光滑，"
        f"保留毛孔细节和绒毛，真实皮肤质感。{creative_text}\n"
        "动作包含：\n"
        f"1. 模特自然展示穿搭：{first_label}\n"
        "2. 模特展示衣服细节\n"
        f"3. 模特自然收尾展示：{last_label}\n"
        "\n要求画面流畅自然，人物皮肤保留瑕疵细节，动态有松弛感，自然流畅，"
        "保持原图光感和色调风格，画面整体真实自然光线和谐统一，9:16竖屏，高清8K。"
        "真实UGC风格。"
    )


def _partition_profile_text(value: str) -> tuple[str, tuple[str, ...]]:
    """Separate standalone prohibitions from positive profile guidance."""

    segments = re.split(
        rf"；|，(?=(?:{'|'.join(_NEGATIVE_PROFILE_PREFIXES)}))",
        value,
    )
    positive: list[str] = []
    negative: list[str] = []
    for segment in segments:
        clause = segment.strip("。；， ")
        if not clause:
            continue
        if clause.startswith(_NEGATIVE_PROFILE_PREFIXES):
            negative.append(clause)
        else:
            positive.append(clause)
    if not positive:
        rendered = ""
    elif len(positive) == 1:
        rendered = positive[0]
    else:
        rendered = f"{positive[0]}；补充：{'；'.join(positive[1:])}"
    return rendered, tuple(negative)


def _style_instruction(
    profile: SiteStyleProfile | None,
    template_id: str,
    action_context: ActionContext | None,
    action_library: Mapping[str, MotionAction] | None,
    learning_preferred_action_ids: tuple[str, ...],
    learning_avoided_action_ids: tuple[str, ...],
    *,
    variant_index: int,
    recent_selected_action_ids: tuple[str, ...],
    recent_motion_combinations: tuple[str, ...],
) -> tuple[str, tuple[str, ...], tuple[str, ...], dict[str, object]]:
    if profile is None:
        return "自然日常；镜头平稳跟随并有限推进", (), (), {}
    base_action_ids = normalize_action_ids(
        (
            *profile.primary_motion_pool,
            *profile.preferred_actions,
        )
        if profile.primary_motion_pool
        else profile.preferred_actions
    )
    allowed = set(base_action_ids)
    preferred = tuple(
        canonical_action_id(action_id)
        for action_id in learning_preferred_action_ids
        if canonical_action_id(action_id) in allowed
    )
    avoided = {
        canonical_action_id(action_id)
        for action_id in learning_avoided_action_ids
        if canonical_action_id(action_id) in allowed
    }
    ordered_action_ids = tuple(
        dict.fromkeys(
            (
                *preferred,
                *(action_id for action_id in base_action_ids if action_id not in avoided),
            )
        )
    )
    use_selector = (
        bool(profile.primary_motion_pool or profile.micro_motion_pool)
        and action_context is not None
    )
    motion_metadata: dict[str, object] = {}
    if use_selector:
        selection = select_brand_motion(
            template_id=template_id,
            primary_motion_pool=ordered_action_ids,
            micro_motion_pool=profile.micro_motion_pool,
            expression_pool=profile.expression_pool,
            camera_pool=profile.camera_pool,
            variant_index=variant_index,
            recent_selected_action_ids=recent_selected_action_ids,
            recent_motion_combinations=recent_motion_combinations,
            dedupe_recent_count=profile.dedupe_recent_count,
            dedupe_combo_window=profile.dedupe_combo_window,
            dedupe_family_window=profile.dedupe_family_window,
            dedupe_family_limit=profile.dedupe_family_limit,
            context=action_context,
            library=action_library,
        )
        selected_action_ids = selection.selected_action_ids
        motion_metadata = {
            key: value
            for key, value in (
                ("selectedPrimaryActionId", selection.primary_action_id),
                ("selectedMicroActionId", selection.micro_action_id),
                ("selectedMotionFamily", selection.family),
                ("selectedMotionCombination", selection.combo_key),
            )
            if value
        }
        preferred_actions = render_action_ids(
            template_id,
            (selection.primary_action_id,) if selection.primary_action_id else (),
            context=action_context,
            library=action_library,
        )
        selected_layer = "；".join(
            part
            for part in (
                f"主动作：{preferred_actions.rstrip('。；')}" if preferred_actions else "",
                (
                    "微动作："
                    + render_action_ids(
                        template_id,
                        (selection.micro_action_id,),
                        context=action_context,
                        library=action_library,
                    ).rstrip("。；")
                )
                if selection.micro_action_id
                else "",
                f"表情：{selection.expression}" if selection.expression else "",
                f"镜头配合：{selection.camera}" if selection.camera else "",
                f"选择依据：{selection.reason}" if selection.reason else "",
            )
            if part
        )
    else:
        selected_action_ids = compatible_action_ids(
            template_id,
            ordered_action_ids,
            context=action_context,
            library=action_library,
        )
        preferred_actions = render_action_ids(
            template_id,
            selected_action_ids,
            context=action_context,
            library=action_library,
        )
        selected_layer = (
            f"可用动作：{preferred_actions.rstrip('。；')}" if preferred_actions else ""
        )
    expression, expression_negatives = _partition_profile_text(profile.expression_language)
    camera, camera_negatives = _partition_profile_text(profile.camera_language)
    pacing, pacing_negatives = _partition_profile_text(_humanize_pacing(profile.pacing))
    return (
        "；".join(
            part
            for part in (
                f"基调：{profile.tone.rstrip('。；')}",
                f"表演（面部清晰时）：{expression}" if expression else "",
                f"镜头：{camera}" if camera else "",
                selected_layer,
                f"节奏：{pacing}" if pacing else "",
                "动作围绕输入中的真实人物、场景与商品自然展开",
            )
            if part
        ),
        selected_action_ids,
        (*expression_negatives, *camera_negatives, *pacing_negatives),
        motion_metadata,
    )


def _learning_instruction(value: str) -> str:
    normalized = " ".join(value.replace("{{", "{").replace("}}", "}").split()).strip()
    if not normalized:
        return ""
    normalized = (
        normalized.replace("延续自然克制", "延续自然流畅")
        .replace("动作克制", "动作自然流畅")
        .replace("更克制", "更自然")
    )
    return f"创作倾向：{normalized[:240]}"


def compile_video_plan(
    value: PromptCompileInput,
    *,
    allow_draft: bool = False,
) -> CompiledVideoPlan:
    template = value.template
    if template.status != "active" and not allow_draft:
        raise ValueError(f"video template is not active: {template.id}")
    if value.site_profile is not None and value.site_profile.status != "active" and not allow_draft:
        raise ValueError(f"site style profile is not active: {value.site_profile.id}")
    if (
        value.site_profile is not None
        and value.site_profile.evidence_level != "verified_site"
        and not allow_draft
    ):
        raise ValueError(f"site style profile evidence is not verified: {value.site_profile.id}")
    if not template.min_images <= value.storyboard_count <= template.max_images:
        raise ValueError("storyboard count is incompatible with video template")
    if not 1 <= value.result_count <= 4:
        raise ValueError("result count must be between 1 and 4")
    if not 1 <= value.variant_index <= value.result_count:
        raise ValueError("variant index must be between 1 and result count")
    if value.shot_action_contexts and len(value.shot_action_contexts) != value.storyboard_count:
        raise ValueError("each storyboard must have its own action context")
    if value.shot_evidence_sources and len(value.shot_evidence_sources) != value.storyboard_count:
        raise ValueError("each storyboard must have its own evidence source")
    if value.shot_face_evidence and len(value.shot_face_evidence) != value.storyboard_count:
        raise ValueError("each storyboard must have its own face evidence")

    action = template.action_presets[value.action_intensity]
    camera = template.camera_presets["standard"]
    variant_action, variant_expression, variant_style = _VARIANT_MODIFIERS[value.variant_index - 1]
    variant_modifier = "；".join(
        (
            f"动作：{variant_action}",
            f"神态（面部清晰时）：{variant_expression}",
            f"呈现：{variant_style}",
        )
    )
    cut_instruction = (
        "智能分镜组织一到三个清楚镜头或视点变化，保持人物、服装与动作连续"
        if value.storyboard_count == 1
        else _cut_instruction(value.cut_mode)
    )
    per_shot = bool(value.shot_action_contexts and value.site_profile)
    if per_shot:
        expression, _ = _partition_profile_text(value.site_profile.expression_language)
        variant_modifier = (
            "按对应分镜所选动作自然推进，表情随面部可见度自然调整"
        )
        performance_instruction = "；".join(
            (
                f"人物表现：{value.site_profile.tone}",
                expression,
                f"节奏：{_humanize_pacing(value.site_profile.pacing)}",
                f"商品重点：{'；'.join(value.site_profile.product_risk_rules)}",
            )
            if expression
            else (
                f"人物表现：{value.site_profile.tone}",
                f"节奏：{_humanize_pacing(value.site_profile.pacing)}",
                f"商品重点：{'；'.join(value.site_profile.product_risk_rules)}",
            )
        )
        selected_action_ids = ()
        style_negatives = ()
        motion_metadata = {}
    else:
        (
            performance_instruction,
            selected_action_ids,
            style_negatives,
            motion_metadata,
        ) = _style_instruction(
            value.site_profile,
            template.id,
            value.action_context,
            value.action_library,
            value.learning_preferred_action_ids,
            value.learning_avoided_action_ids,
            variant_index=value.variant_index,
            recent_selected_action_ids=value.recent_selected_action_ids,
            recent_motion_combinations=value.recent_motion_combinations,
        )
    duration_seconds = value.duration_seconds or template.duration_seconds
    if not 3 <= duration_seconds <= 15:
        raise ValueError("duration must be between 3 and 15 seconds")
    duration = float(duration_seconds)
    shot_duration = duration / value.storyboard_count
    shot_motion: list[tuple[str, dict[str, object]]] = []
    if per_shot:
        shot_recent_actions = list(value.recent_selected_action_ids)
        shot_recent_combos = list(value.recent_motion_combinations)
        for index, context in enumerate(value.shot_action_contexts, 1):
            instruction, metadata = plan_shot_motion(
                profile=value.site_profile,
                template_id=template.id,
                context=context,
                duration=shot_duration,
                variant_index=value.variant_index,
                library=value.action_library,
                preferred=value.learning_preferred_action_ids,
                avoided=value.learning_avoided_action_ids,
                recent_actions=tuple(shot_recent_actions),
                recent_combos=tuple(shot_recent_combos),
                selection_salt=value.selection_salt,
                face_evidence=(
                    value.shot_face_evidence[index - 1]
                    if value.shot_face_evidence
                    else FaceEvidence()
                ),
            )
            metadata["order"] = index
            metadata["evidenceSource"] = (
                value.shot_evidence_sources[index - 1] if value.shot_evidence_sources else "unknown"
            )
            shot_motion.append((instruction, metadata))
            shot_recent_actions.insert(0, str(metadata["selectedPrimaryActionId"]))
            shot_recent_combos.insert(0, str(metadata["selectedMotionCombination"]))
        selected_action_ids = tuple(
            dict.fromkeys(
                action_id
                for _, metadata in shot_motion
                for action_id in metadata["selectedActionIds"]
            )
        )
        motion_metadata = {
            "shotMotionSelections": [metadata for _, metadata in shot_motion],
            "selectedPrimaryActionId": shot_motion[0][1]["selectedPrimaryActionId"],
            "selectedMotionCombination": shot_motion[0][1]["selectedMotionCombination"],
        }
    shots = tuple(
        ShotInstruction(
            order=index,
            start_seconds=round((index - 1) * shot_duration, 2),
            end_seconds=round(
                duration if index == value.storyboard_count else index * shot_duration,
                2,
            ),
            instruction=(
                shot_motion[index - 1][0]
                if per_shot
                else _shot_rule(template, index, value.storyboard_count)
                if value.storyboard_count == 1
                else f"分镜{index}：{_shot_rule(template, index, value.storyboard_count)}"
            ),
        )
        for index in range(1, value.storyboard_count + 1)
    )
    invariants = tuple(
        item
        for item in template.prompt_rules.invariants
        if value.storyboard_count > 1 or not item.startswith("按输入分镜顺序执行")
    )
    # Keep the complete catalog in plan metadata for review, but do not render
    # every named site mismatch as provider prompt text. Unrelated concepts can
    # dilute the current shot and accidentally prime content.
    profile_negatives = (
        tuple(
            dict.fromkeys(
                (
                    *value.site_profile.forbidden_mismatches,
                    *(
                        value.site_profile.forbidden_motion
                        if value.action_context is not None
                        else ()
                    ),
                )
            )
        )
        if value.site_profile
        else ()
    )
    negatives = tuple(
        dict.fromkeys((*template.prompt_rules.negatives, *profile_negatives, *style_negatives))
    )
    negative_prompt = "；".join(negatives)
    rendered_negative_prompt = (
        "人物或服装明显变形；商品图案、Logo或文字漂移；"
        "无目的甩镜或旋转；画面外文字"
    )
    timeline = "；".join(
        (
            f"{shot.start_seconds:g}-{shot.end_seconds:g}秒：{shot.instruction}"
            if value.storyboard_count == 1
            else (
                f"{shot.start_seconds:g}-{shot.end_seconds:g}秒/图{shot.order}：{shot.instruction}"
            )
        )
        for shot in shots
    )
    learning_instruction = _learning_instruction(value.learning_guidance)
    motion_instruction = "；".join(
        part
        for part in (
            f"时间轴：{timeline}",
            f"切镜：{cut_instruction}",
            (
                "整体节奏：按整条视频的创意推进安排动作与切镜。景别上，相邻镜头优先通过取景或构图形成可感知差异；"
                "可组合全景建立人物与环境关系、"
                "中景展示整体穿搭、近景表达情绪、特写呈现商品细节，按创意和素材灵活取舍，不必凑齐；"
                "切换场景时也避免只换背景而画面构图近似"
            )
            if value.storyboard_count > 1
            else "",
            f"幅度：{_AMPLITUDE_GUIDANCE[value.action_intensity]}"
            if per_shot
            else f"幅度：{action.instruction}",
        )
        if part
    )
    performance_parts = [performance_instruction]
    if value.result_count > 1:
        performance_parts.append(f"本条变化：{variant_modifier}")
    if value.site_profile is None:
        performance_parts.append(camera.instruction)
    creative_brief = " ".join(value.creative_brief.split()).strip()
    creative_instruction = (
        f"用户创意目标：{creative_brief}。围绕目标设计清楚连续的动作和镜头推进"
        if creative_brief
        else ""
    )
    prompt_parts = {
        "task": (
            "任务："
            + (
                "基于输入图"
                if value.storyboard_count == 1
                else f"按{value.storyboard_count}张分镜顺序"
            )
            + f"生成{duration_seconds}秒、{template.ratio}的{template.name}视频"
        ),
        "creative": creative_instruction,
        "continuity": f"保持：{'；'.join(invariants)}",
        "material": f"服装：{garment_material_guidance(value.garment_material)}",
        "action": f"动作：{motion_instruction}",
        "performance": f"表演与镜头：{'；'.join(performance_parts)}",
        "learning": learning_instruction,
        "capture": (
            "真实成像：普通手机后摄的可见景深与自然光，人物、服装与场景处于同一光场，"
            "真实皮肤和布料纹理，商品颜色、图案与文字清楚"
        ),
        "avoid": f"避免：{rendered_negative_prompt}",
    }

    def render_prompt() -> str:
        return "。".join(
            part.rstrip("。； ")
            for part in prompt_parts.values()
            if part and part.strip()
        )

    master_prompt = render_prompt()
    prompt_budget = min(value.prompt_budget, _MAX_KLING_PROMPT_CHARS)
    if prompt_budget < 1:
        raise ValueError("prompt budget must be positive")
    compression_level = "full"
    if len(master_prompt) > prompt_budget and learning_instruction:
        overflow = len(master_prompt) - prompt_budget
        keep = max(0, len(learning_instruction) - overflow - 1)
        learning_instruction = learning_instruction[:keep].rstrip("；，。 ")
        prompt_parts["learning"] = learning_instruction
        master_prompt = render_prompt()
        compression_level = "trimmed-learning"
    if len(master_prompt) > prompt_budget:
        compact_style = (
            f"人物表现：{value.site_profile.tone.rstrip('。；')}；"
            if value.site_profile is not None
            else ""
        )
        prompt_parts["performance"] = (
            f"表演与镜头：{compact_style}动作自然展开"
        )
        master_prompt = render_prompt()
        compression_level = "compact-style"
    if len(master_prompt) > prompt_budget:
        prompt_parts["capture"] = (
            "真实成像：普通手机后摄、自然现场光、真实皮肤与布料纹理，商品颜色和图案可读"
        )
        master_prompt = render_prompt()
        compression_level = "compact-quality"
    ugc_action_ids: tuple[str, str] | None = None
    if template.id == "ootd_walk":
        ugc_action_ids = _ootd_ugc_action_ids(value, shot_motion, selected_action_ids)
        master_prompt = _render_ootd_ugc_prompt(
            material=value.garment_material,
            first_action_id=ugc_action_ids[0],
            last_action_id=ugc_action_ids[1],
            creative_brief=value.creative_brief,
            library=value.action_library or ACTION_LIBRARY,
        )
        compression_level = "ootd-ugc"
    if len(master_prompt) > prompt_budget:
        raise ValueError(f"compiled prompt exceeds budget: {len(master_prompt)} > {prompt_budget}")
    return CompiledVideoPlan(
        template_id=template.id,
        template_version=template.version,
        master_prompt=master_prompt,
        shot_instructions=shots,
        invariants=invariants,
        negative_prompt=negative_prompt,
        variant_seed={
            "variantIndex": value.variant_index,
            "actionIntensity": value.action_intensity,
            "cutMode": value.cut_mode,
            "modifier": variant_modifier,
            "pacingSource": "site-profile" if value.site_profile else "template-default",
            "siteProfileId": value.site_profile.id if value.site_profile else "none",
            "garmentMaterial": value.garment_material,
            "selectedActionIds": list(selected_action_ids),
            **({"ugcActionIds": list(ugc_action_ids)} if ugc_action_ids else {}),
            **motion_metadata,
            "recentSelectedActionIds": list(value.recent_selected_action_ids),
            "recentMotionCombinations": list(value.recent_motion_combinations),
            "learningProfileVersion": value.learning_profile_version,
            "learningEvidenceCount": value.learning_evidence_count,
            "selectionSalt": value.selection_salt,
            "promptCompressionLevel": compression_level,
        },
        site_profile_id=value.site_profile.id if value.site_profile else None,
        site_profile_version=value.site_profile.version if value.site_profile else None,
    )
