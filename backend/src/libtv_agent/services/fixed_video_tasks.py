from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.video_provider import VideoProviderAdapter
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import ordered_assets_hash, sha256_json
from libtv_agent.domain.enums import AttemptStatus, CommandStatus, ParentTaskPhase, VariantKind
from libtv_agent.domain.models import (
    Asset,
    AuditEvent,
    GenerationCommand,
    GenerationVariant,
    OutboxEvent,
    ParentTask,
    StagedUpload,
    SubjectElementJob,
    User,
)
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services.video_type_release import (
    UNBOXING_PAUSED_CODE, UNBOXING_PAUSED_MESSAGE, unboxing_submission_paused,
)
from libtv_agent.services.account_access import platform_admin, review_users
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_PLANNER_VERSION,
    V25_EXPERIMENTAL_VERSION,
    V25_PROMPT_MAX_CHARS,
    CreativePlannerError,
    plan_creative_video,
    production_v25_enabled,
)
from libtv_agent.services.automatic_motion_facts import observe_storyboards
from libtv_agent.services.cutflow_budget import reserve_auto_cutflow
from libtv_agent.services.content_catalog import (
    get_merged_site_style_profile,
    motion_action_library,
)
from libtv_agent.services.feedback_learning import apply_video_learning
from libtv_agent.services.generation_safety import paid_generation_authorized, write_preview_file
from libtv_agent.services.kling_cli_cost import credits_to_microusd, estimate_cli_credits
from libtv_agent.services.kling_preparation import prepare_kling_task
from libtv_agent.services.material_validator import MaterialDescriptor, validate_materials
from libtv_agent.services.ootd_cross_scene import product_analysis_enabled
from libtv_agent.services.ootd_direction import creative_controls, use_product_evidence
from libtv_agent.services.points import reserve_points
from libtv_agent.services.prompt_compiler import PromptCompileInput, compile_video_plan
from libtv_agent.services.runtime_control import (
    RuntimeControlBlockedError,
    ensure_provider_submission_allowed,
)
from libtv_agent.services.subject_elements import automatic_element_metadata
from libtv_agent.services.tasks import get_task
from libtv_agent.templates.garment_materials import (
    GarmentMaterial,
    infer_garment_material,
)
from libtv_agent.templates.motion_actions import ActionContext
from libtv_agent.templates.site_style_profiles import SiteStyleProfile
from libtv_agent.templates.video_templates import get_video_template
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# A privileged local operator may mark one already-accepted, failed task for
# one V2.5 retry. The API request schema cannot set this internal task marker.
SOL_V25_PILOT_MARKER = "sol-v25-single-task-20261101"


def _task_specific_v25_enabled(settings: Settings, task: ParentTask | None) -> bool:
    if production_v25_enabled(settings):
        return True
    return bool(
        task is not None
        and settings.openai_model.strip().casefold() in {"gpt-6.1-sol", "gpt-6.2-sol"}
        and settings.ai_adapter_mode == "openai"
        and task.workflow_kind == "FIXED_TEMPLATE"
        and isinstance(task.settings_json, dict)
        and task.settings_json.get("_aiCreativeV25Pilot") == SOL_V25_PILOT_MARKER
    )


DEFAULT_VIDEO_TEMPLATE_ID = "ootd_walk"
AUTOMATIC_PROMPT_COMPILER_VERSION = "fixed-template-v11-ugc-actions"
CUSTOM_PROMPT_COMPILER_VERSION = "custom-direct-v1"
MAX_KLING_PROMPT_CHARS = 3072
logger = logging.getLogger(__name__)


async def _recent_motion_history(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    template_id: str,
    site_profile: SiteStyleProfile | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if site_profile is None or not site_profile.primary_motion_pool:
        return (), ()
    rows = await session.scalars(
        select(GenerationVariant.variant_seed_json)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(
            ParentTask.user_id == user_id,
            ParentTask.template_id == template_id,
            ParentTask.site_profile_id == site_profile.id,
        )
        .order_by(GenerationVariant.created_at.desc())
        .limit(max(site_profile.dedupe_family_window, site_profile.dedupe_combo_window)),
    )
    selected_ids: list[str] = []
    combinations: list[str] = []
    for seed in rows:
        primary = seed.get("selectedPrimaryActionId")
        if isinstance(primary, str) and primary:
            selected_ids.append(primary)
        else:
            legacy_ids = seed.get("selectedActionIds", ())
            if isinstance(legacy_ids, list | tuple):
                selected_ids.extend(
                    item for item in legacy_ids[:1] if isinstance(item, str) and item
                )
        combination = seed.get("selectedMotionCombination")
        if isinstance(combination, str) and combination:
            combinations.append(combination)
    return (
        tuple(selected_ids[: site_profile.dedupe_family_window]),
        tuple(combinations[: site_profile.dedupe_combo_window]),
    )


async def _recent_ootd_prompts(
    session: AsyncSession, *, user_id: uuid.UUID, site_profile_id: str | None,
    exclude_task_id: uuid.UUID | None,
) -> tuple[str, ...]:
    """Only the 3 latest distinct confirmed OOTD tasks for this user/site."""
    from libtv_agent.services.ootd_simple import HISTORY_PROFILES

    query = (
        select(GenerationVariant.parent_task_id, GenerationVariant.prompt_text)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(
            ParentTask.user_id == user_id,
            ParentTask.workflow_kind == "FIXED_TEMPLATE",
            GenerationVariant.prompt_template_version.in_(HISTORY_PROFILES),
            GenerationVariant.variant_type == VariantKind.STABLE,
            GenerationVariant.confirmed_at.is_not(None),
            GenerationVariant.created_at >= datetime.now(UTC) - timedelta(days=14),
        )
        .order_by(GenerationVariant.created_at.desc())
        .limit(20)
    )
    if site_profile_id:
        query = query.where(ParentTask.site_profile_id == site_profile_id)
    if exclude_task_id:
        query = query.where(ParentTask.id != exclude_task_id)
    rows = (await session.execute(query)).all()
    seen: set[uuid.UUID] = set()
    prompts: list[str] = []
    for parent_id, content in rows:
        if parent_id in seen or not isinstance(content, str) or not content.strip():
            continue
        seen.add(parent_id)
        prompts.append(content)
        if len(prompts) == 3:
            break
    return tuple(prompts)


class FixedVideoTaskValidationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _resolve_garment_material(
    observations: list[Any],
    *,
    title: str,
    creative_brief: str,
    ordered_inputs: list[Any],
    ordered_subject_inputs: list[Any],
    by_id: dict[uuid.UUID, StagedUpload],
) -> tuple[GarmentMaterial, str]:
    observed = {
        observed.garment_material
        for observed in observations
        if observed.garment_material != "unknown"
    }
    if len(observed) == 1:
        found = observed.pop()
        # Vision sees the knit surface, task text distinguishes cardigan construction.
        if (
            found == "short_mohair"
            and infer_garment_material(title, creative_brief) == "short_mohair_cardigan"
        ):
            return "short_mohair_cardigan", "automatic_vision_and_task_text"
        return found, "automatic_vision"
    filenames = [
        by_id[item.upload_id].original_filename
        for item in [*ordered_inputs, *ordered_subject_inputs]
    ]
    inferred = infer_garment_material(title, creative_brief, *filenames)
    return (inferred, "task_text") if inferred != "unknown" else ("unknown", "unknown")


def _validate_final_prompt_length(
    prompt: str, *, code: str, message: str,
    planner_version: str | None = None,
    provider_backend: str | None = None,
) -> str:
    # Only explicit V2.5 outputs targeting the Kling 4.0 API can use the
    # expanded planner budget. Ordinary, legacy and custom prompts stay at 3072.
    limit = (
        V25_PROMPT_MAX_CHARS
        if planner_version == V25_EXPERIMENTAL_VERSION and provider_backend == "api"
        else MAX_KLING_PROMPT_CHARS
    )
    if len(prompt) > limit:
        raise FixedVideoTaskValidationError(
            code, message, details={"finalLength": len(prompt), "limit": limit},
        )
    return prompt


def _is_retired_short_mohair_video_copy(prompt: str) -> bool:
    normalized = "".join(prompt.split()).casefold()
    return (
        "ugclifestylescene" in normalized
        and "不要连续一镜到底" in normalized
        and "没有螺纹，没有螺纹" in normalized
    )


def _paid_boundary(provider: str, settings: Settings) -> str:
    if provider == settings.kling_api_provider_name:
        return "Kling official API omni video generation"
    if provider == settings.kling_cli_provider_name:
        return "Kling official CLI historical recovery"
    return f"{provider} video generation"


def _validation_snapshot(result: Any) -> dict[str, Any]:
    return {
        "valid": result.valid,
        "errors": [asdict(issue) for issue in result.errors],
        "warnings": [asdict(issue) for issue in result.warnings],
        "visionCheckRequired": result.vision_check_required,
    }


def _reference_role_prompt(
    *,
    storyboard_count: int,
    subject_image_count: int,
    element_count: int,
) -> str:
    """Describe ordered Kling references without making the product image a shot source."""

    storyboard_ids = ", ".join(f"@image_{index}" for index in range(1, storyboard_count + 1))
    if storyboard_count == 1:
        instructions = [
            f"References: {storyboard_ids} is the source image controlling identity, product, "
            "scene, lighting, composition, and the starting pose. The visible pose is a starting "
            "anchor; execute the planned action progression and do not freeze the video at "
            "that pose."
        ]
    else:
        instructions = [
            f"References: {storyboard_ids} are ordered visual references controlling identity, "
            "product, scene, composition, and shot order. Their visible poses are starting "
            "anchors; "
            "follow the planned action progression with continuous visible motion between and "
            "within shots, and do not output a still-image slideshow."
        ]
    if subject_image_count:
        first_subject_index = storyboard_count + 1
        subject_roles = [f"@image_{first_subject_index} is the primary product reference"]
        if subject_image_count >= 2:
            if subject_image_count == 2:
                subject_roles.append(
                    f"@image_{first_subject_index + 1} is a supplementary product reference"
                )
            else:
                subject_roles.append(
                    f"@image_{first_subject_index + 1}"
                    f" through @image_{first_subject_index + subject_image_count - 1}"
                    " are supplementary product references"
                )
        instructions.append(
            "; ".join(subject_roles)
            + ". Product references guide the product's visible appearance, style, print, "
            "logo, and visible text only; do not use them to replace the people, scene, "
            "composition, starting pose, planned action, or camera language from the source images."
        )
    elif element_count:
        element_ids = ", ".join(f"@element_{index}" for index in range(1, element_count + 1))
        instructions.append(
            f"{element_ids} are historical subject elements constraining identity and "
            "appearance only."
        )
    return " ".join(instructions)


async def _mark_action_required(
    session: AsyncSession,
    task: ParentTask,
    *,
    actor_id: uuid.UUID,
    code: str,
    message: str,
) -> ParentTask:
    from_phase = task.phase
    task.phase = ParentTaskPhase.ACTION_REQUIRED
    task.phase_reason_code = code
    task.state_version += 1
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="FIXED_VIDEO_PREFLIGHT_BLOCKED",
            entity_type="parent_task",
            entity_id=str(task.id),
            from_state=from_phase.value,
            to_state=ParentTaskPhase.ACTION_REQUIRED.value,
            reason=message,
            correlation_id=str(uuid.uuid4()),
            evidence_refs_json=[],
        )
    )
    await session.commit()
    return await get_task(session, task.user_id, task.id)


async def create_fixed_video_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: VideoTaskCreate,
    settings: Settings,
    kling_adapter: VideoProviderAdapter,
    object_store: ObjectStore,
    existing_task: ParentTask | None = None,
    expected_analysis_run_id: uuid.UUID | None = None,
) -> ParentTask:
    # Preserve already-accepted jobs, but refuse any new frozen-mode task.
    if existing_task is None and unboxing_submission_paused(payload.video_type):
        raise FixedVideoTaskValidationError(UNBOXING_PAUSED_CODE, UNBOXING_PAUSED_MESSAGE)
    owner = await session.get(User, user_id)
    session.info["brand_id"] = owner.brand_id if owner else None
    session.info["actor_id"] = user_id
    from libtv_agent.services.brand_identity import append_brand_reference
    from libtv_agent.services.cutflow_site import CutflowSiteError, creation_snapshot

    try:
        source_site = await creation_snapshot(session, user_id, payload.site_profile_id)
    except CutflowSiteError as exc:
        raise FixedVideoTaskValidationError("CUTFLOW_SITE_REQUIRED", str(exc)) from exc
    if owner and not platform_admin(owner) and payload.element_ids:
        allowed = set(
            await session.scalars(
                select(SubjectElementJob.element_id).where(
                    SubjectElementJob.user_id.in_(review_users(owner)),
                    SubjectElementJob.element_id.in_(payload.element_ids),
                )
            )
        )
        if set(payload.element_ids) - allowed:
            raise FixedVideoTaskValidationError(
                "ELEMENT_NOT_IN_BRAND", "主体不在当前账号权限范围内"
            )
    if not payload.paid_submission_consent:
        raise FixedVideoTaskValidationError(
            "PAID_SUBMISSION_CONSENT_REQUIRED",
            "点击开始生成前必须明确同意提交付费可灵生成任务",
        )
    if settings.kling_provider_backend == "cli" and payload.element_ids:
        raise FixedVideoTaskValidationError(
            "KLING_ELEMENTS_UNSUPPORTED",
            "当前 Kling CLI 生成通道尚不支持历史 Element 绑定，请上传参考图片。",
        )
    if (
        payload.generation_mode == "custom"
        and payload.custom_prompt
        and _is_retired_short_mohair_video_copy(payload.custom_prompt)
    ):
        raise FixedVideoTaskValidationError(
            "RETIRED_VIDEO_PROMPT_PRESET",
            "该示例提示词已停用；请使用自动编排，由输入图、材质识别和动作库生成提示词",
        )

    try:
        template = get_video_template(
            payload.template_id or DEFAULT_VIDEO_TEMPLATE_ID,
            require_active=True,
        )
    except ValueError as exc:
        raise FixedVideoTaskValidationError("VIDEO_TEMPLATE_NOT_ACTIVE", str(exc)) from exc
    site_profile = None
    actions = {}
    if payload.generation_mode == "automatic":
        try:
            site_profile = await get_merged_site_style_profile(
                session,
                source_site["profileId"] if source_site else payload.site_profile_id or "",
                require_active=True,
            )
        except ValueError as exc:
            raise FixedVideoTaskValidationError("SITE_PROFILE_NOT_ACTIVE", str(exc)) from exc
        # Legacy action selection remains only for offline fake/replay fixtures.
        # The live AI director must not select or depend on library actions.
        if settings.ai_adapter_mode != "openai" and not settings.ai_ootd_v1_enabled:
            actions = await motion_action_library(session)
    ordered_inputs = sorted(payload.storyboards, key=lambda item: item.order)
    ordered_subject_inputs = sorted(payload.subject_images, key=lambda item: item.order)
    upload_ids = [item.upload_id for item in [*ordered_inputs, *ordered_subject_inputs]]
    uploads = list(
        await session.scalars(
            select(StagedUpload).where(
                StagedUpload.id.in_(upload_ids),
                StagedUpload.user_id == user_id,
                (StagedUpload.claimed_task_id == existing_task.id)
                if existing_task is not None else StagedUpload.claimed_task_id.is_(None),
            )
        )
    )
    by_id = {upload.id: upload for upload in uploads}
    if set(by_id) != set(upload_ids):
        raise FixedVideoTaskValidationError(
            "STORYBOARD_UPLOAD_UNAVAILABLE",
            "一个或多个分镜图已失效、已被使用或不属于当前用户",
        )

    descriptors = [
        MaterialDescriptor(
            id=str(upload.id),
            order=item.order,
            original_name=upload.original_filename,
            mime_type=upload.mime_type,
            size_bytes=upload.size_bytes,
            sha256=upload.sha256,
            width=upload.width,
            height=upload.height,
        )
        for item in ordered_inputs
        for upload in (by_id[item.upload_id],)
    ]
    validation = validate_materials(
        descriptors,
        template=template,
        variant_count=payload.result_count,
        action_intensity=payload.action_intensity,
        camera_tempo="standard",
        cut_mode=payload.cut_mode,
        # Motion eligibility is resolved automatically per shot below. Structural
        # validation must not advertise a pending manual visual review.
        requested_action_ids=(),
        action_library=actions,
        max_file_bytes=settings.max_input_image_bytes,
    )
    validation_json = _validation_snapshot(validation)
    if not validation.valid:
        raise FixedVideoTaskValidationError(
            "MATERIAL_VALIDATION_FAILED",
            "分镜素材未通过固定模板校验",
            details=validation_json,
        )

    role_prompt = _reference_role_prompt(
        storyboard_count=len(ordered_inputs),
        subject_image_count=len(ordered_subject_inputs),
        element_count=len(payload.element_ids),
    )
    role_prompt = append_brand_reference(
        role_prompt, source_site["brandReference"] if source_site else None,
    )
    from libtv_agent.services.ootd_simple import (
        active_ootd_profile,
    )
    from libtv_agent.services.ootd_simple import (
        eligible as ootd_eligible,
    )
    from libtv_agent.services.ootd_simple import (
        plan_simple_ootd,
    )
    ootd_enabled = settings.ai_ootd_v1_enabled and payload.generation_mode == "automatic"
    analyze_products = use_product_evidence(
        payload.display_focus, payload.sol_analyze_product_images, len(ordered_subject_inputs),
    )
    if ootd_enabled and product_analysis_enabled(
        video_type=payload.video_type, product_count=len(ordered_subject_inputs),
    ):
        analyze_products = True  # Sol sees the original garment, not only Kling.
    OOTD_PROFILE = active_ootd_profile(
        settings, video_type=payload.video_type, director_mode=payload.director_mode,
        product_count=len(ordered_subject_inputs), analyze_product_images=analyze_products,
    )
    ootd_controls = creative_controls(
        focus=payload.display_focus, analyze_products=analyze_products,
        brand_tone=site_profile.tone if site_profile else "",
        action_intensity=payload.action_intensity, variant_count=payload.result_count,
    ) if ootd_enabled else None
    if ootd_enabled:
        if not ootd_eligible(
            generation_mode=payload.generation_mode, cut_mode=payload.cut_mode,
            provider=settings.kling_provider_backend,
            model=settings.active_kling_model or "", ai_model=settings.openai_model,
            duration=payload.duration_seconds,
        ) or settings.ai_adapter_mode != "openai":
            raise FixedVideoTaskValidationError(
                "OOTD_ROUTE_UNAVAILABLE",
                "OOTD需要Kling CLI/API 3.0 Omni、GPT-6.1 Sol和明确切镜；本次不回退USP。",
            )
        if not 1 <= len(ordered_inputs) <= 7:
            raise FixedVideoTaskValidationError(
                "OOTD_STORYBOARD_COUNT", "OOTD分镜图数量应为1至7张（首张作为视频起始帧）。",
            )
    from libtv_agent.services.native_five_shot import native_i_eligible
    from libtv_agent.services.native_v34_director import (
        PROFILE as V34_PROFILE,
    )
    from libtv_agent.services.native_v34_director import (
        native_v34_eligible,
    )
    native_v34 = not ootd_enabled and native_v34_eligible(
        enabled=settings.ai_director_v34_enabled,
        generation_mode=payload.generation_mode, duration=payload.duration_seconds,
        cut_mode=payload.cut_mode, provider=settings.kling_provider_backend,
        model=settings.active_kling_model or "", ai_model=settings.openai_model,
    )
    native_i = not ootd_enabled and native_i_eligible(
        enabled=settings.fastcut_ugc_pacing_v1_enabled,
        generation_mode=payload.generation_mode, duration=payload.duration_seconds,
        cut_mode=payload.cut_mode, provider=settings.kling_provider_backend,
        model=settings.active_kling_model or "", ai_model=settings.openai_model,
    )
    native_i = native_i and not native_v34  # V3.4 takes precedence; I remains rollback.
    task_uses_v25 = not ootd_enabled and (native_i or native_v34 or (
        payload.generation_mode == "automatic"
        and settings.ai_adapter_mode == "openai"
        and _task_specific_v25_enabled(settings, existing_task)
    ))
    if payload.generation_mode == "automatic" and settings.ai_adapter_mode == "openai":
        # The visual evidence and creative plan are both generated before task creation.
        # A failed grounding/planning request cannot reach the paid video provider.
        from libtv_agent.adapters.openai_analysis import AnalysisImageInput
        from libtv_agent.services.analysis import _analysis_image_url

        async def planner_input(item, order: int, role: str):
            upload = by_id[item.upload_id]
            image_url = await _analysis_image_url(
                object_store, upload.object_key, str(upload.id), settings.artifact_root,
            )
            return AnalysisImageInput(
                asset_id=str(upload.id), order=order, image_url=image_url, role=role,
            )

        try:
            storyboard_images = [
                await planner_input(item, index, "STORYBOARD")
                for index, item in enumerate(ordered_inputs, 1)
            ]
            # In simple OOTD mode, the original product uploads still go to
            # Kling, but their pixels need not be read/encoded for Sol unless
            # the user explicitly opts in to product-aware shot planning.
            if ootd_enabled and not analyze_products:
                product_images = [
                    AnalysisImageInput(
                        asset_id=str(item.upload_id), order=index,
                        image_url="", role="PRODUCT",
                    )
                    for index, item in enumerate(ordered_subject_inputs, 1)
                ]
            else:
                product_images = [
                    await planner_input(item, index, "PRODUCT")
                    for index, item in enumerate(ordered_subject_inputs, 1)
                ]
            # Preserve the production SQLite lock release before inference.
            await session.commit()
            if ootd_enabled:
                recent_ootd_prompts: tuple[str, ...] = ()
                try:
                    # A savepoint keeps an optional history-query error from
                    # poisoning the task transaction or creating a new gate.
                    async with session.begin_nested():
                        recent_ootd_prompts = await _recent_ootd_prompts(
                            session,
                            user_id=user_id,
                            site_profile_id=(
                                source_site["profileId"] if source_site
                                else payload.site_profile_id
                            ),
                            exclude_task_id=existing_task.id if existing_task else None,
                        )
                except Exception:
                    logger.warning("OOTD recent motion history unavailable")
                # Release the short history-read transaction before Sol inference;
                # long-running model calls must never hold a SQLite read lock.
                await session.commit()
                creative_variants = await plan_simple_ootd(
                    settings=settings, storyboard_images=storyboard_images,
                    product_images=product_images,
                    analyze_product_images=analyze_products,
                    display_focus=payload.display_focus if payload.video_type == "ootd" else "overall",
                    video_type=payload.video_type,
                    director_mode=payload.director_mode,
                    brand_tone=str(ootd_controls["brandTone"]),
                    action_intensity=payload.action_intensity,
                    creative_brief=payload.creative_brief,
                    duration_seconds=payload.duration_seconds,
                    variant_count=payload.result_count,
                    recent_prompts=recent_ootd_prompts if payload.video_type == "ootd" else (),
                )
            else:
                creative_variants = await plan_creative_video(
                    settings=settings,
                    storyboard_images=storyboard_images,
                    product_images=product_images,
                    reference_role_prompt=role_prompt,
                    creative_brief=payload.creative_brief.strip(),
                    brand_tone=site_profile.tone if site_profile else "",
                    target_audience=site_profile.audience if site_profile else "",
                    product_priorities=(site_profile.product_priorities if site_profile else ()),
                    forbidden_mismatches=(
                        site_profile.forbidden_mismatches if site_profile else ()
                    ),
                    garment_material=(
                        infer_garment_material(payload.creative_brief)
                        if infer_garment_material(payload.creative_brief) != "unknown"
                        else infer_garment_material(
                            payload.title,
                            *(
                                by_id[item.upload_id].original_filename
                                for item in [*ordered_inputs, *ordered_subject_inputs]
                            ),
                        )
                    ),
                    duration_seconds=payload.duration_seconds,
                    variant_count=payload.result_count,
                    cut_mode=payload.cut_mode,
                    action_intensity=payload.action_intensity,
                    experimental_v25=task_uses_v25,
                    native_five_shot=native_i,
                    native_v34=native_v34,
                    marketing_mode="usp" if native_v34 else "showcase",
                )
            prompt_texts = [
                _validate_final_prompt_length(
                    variant.prompt,
                    code="COMPILED_PROMPT_TOO_LONG",
                    message="AI创意提示词超过模型字符限制",
                    planner_version=variant.planner_version,
                    provider_backend=settings.kling_provider_backend,
                )
                for variant in creative_variants
            ]
        except CreativePlannerError as exc:
            # An upstream outage/auth/schema error is not a reference-image failure.
            raise FixedVideoTaskValidationError(
                exc.code,
                "AI编排未完成，本次未提交视频生成。",
                details={
                    "aiPlannerRequestAttempts": exc.request_attempts,
                    **({"aiPlannerValidationRule": exc.validation_rule}
                       if exc.validation_rule else {}),
                    **(
                        {"upstreamHttpStatus": exc.upstream_status}
                        if exc.upstream_status is not None else {}
                    ),
                },
            ) from exc
        except Exception as exc:
            raise FixedVideoTaskValidationError(
                "AI_CREATIVE_RESPONSE_INVALID",
                "AI编排结果处理失败，本次未提交视频生成。",
            ) from exc
        variant_seeds = [
            {
                "variantIndex": index,
                "planner": variant.planner_version,
                "visualGroundingVerified": all(
                    fact["person_count_verified"] for fact in variant.facts
                ),
                "creativeIdea": variant.creative_idea,
                "visualHook": variant.visual_hook,
                "buyerReason": variant.buyer_reason,
                "aiPlannerRequestAttempts": variant.request_attempts,
                "selectedActionIds": [],
            }
            for index, variant in enumerate(creative_variants, 1)
        ]
        plan_json = {
            "compiler": (
                (OOTD_PROFILE if payload.director_mode == "cross_scene_v2" else "allwe-ootd-sol-simple-v1") if ootd_enabled else V25_EXPERIMENTAL_VERSION if task_uses_v25
                else CREATIVE_PLANNER_VERSION
            ),
            "variants": [
                {
                    "master_prompt": prompt,
                    "shot_instructions": list(variant.beats),
                    "visual_facts": list(variant.facts),
                    "creative_idea": variant.creative_idea,
                    "visual_hook": variant.visual_hook,
                    "buyer_reason": variant.buyer_reason,
                    "variant_seed": seed,
                }
                for prompt, variant, seed in zip(
                    prompt_texts, creative_variants, variant_seeds, strict=True,
                )
            ],
        }
        validation_json["aiCreativePlanner"] = {
            "version": (
                (OOTD_PROFILE if payload.director_mode == "cross_scene_v2" else "allwe-ootd-sol-simple-v1") if ootd_enabled else V25_EXPERIMENTAL_VERSION if task_uses_v25
                else CREATIVE_PLANNER_VERSION
            ),
            "requestAttempts": max(v.request_attempts for v in creative_variants),
            "verified": all(
                fact["person_count_verified"]
                for variant in creative_variants for fact in variant.facts
            ),
            "storyboardCount": len(ordered_inputs),
            "variantCount": len(creative_variants),
        }
    elif payload.generation_mode == "automatic":
        # Fake/replay environments retain their historical fixtures for offline
        # compatibility; a live paid runtime must never silently use old actions.
        if paid_generation_authorized(settings) and settings.app_env != "test":
            raise FixedVideoTaskValidationError(
                "AI_CREATIVE_PLAN_UNAVAILABLE",
                "未配置AI视觉创意编排，不允许回退旧动作模板提交付费视频。",
            )
        observations = await observe_storyboards(
            session,
            uploads=[by_id[item.upload_id] for item in ordered_inputs],
            user_id=user_id,
            brand_id=owner.brand_id if owner else None,
            settings=settings,
            object_store=object_store,
        )
        descriptors = [
            replace(
                material,
                confirmed_action_capabilities=observed.context.confirmed,
                denied_action_capabilities=observed.context.denied,
            )
            for material, observed in zip(descriptors, observations, strict=True)
        ]
        garment_material, garment_material_source = _resolve_garment_material(
            observations,
            title=payload.title,
            creative_brief=payload.creative_brief,
            ordered_inputs=ordered_inputs,
            ordered_subject_inputs=ordered_subject_inputs,
            by_id=by_id,
        )
        validation_json["garmentMaterial"] = {
            "kind": garment_material,
            "source": garment_material_source,
            "perStoryboard": [
                observed.garment_material for observed in observations
            ],
        }
        validation_json["storyboardActionFacts"] = [
            {
                "uploadId": material.id,
                "order": material.order,
                "sha256": material.sha256,
                "source": observed.source,
                "reason": observed.reason,
                "confirmed": sorted(c.value for c in observed.context.confirmed),
                "denied": sorted(c.value for c in observed.context.denied),
                "faceEvidence": asdict(observed.face_evidence),
            }
            for material, observed in zip(descriptors, observations, strict=True)
        ]
        learning = await apply_video_learning(
            session,
            user_id=user_id,
            template_id=template.id,
            site_profile_id=site_profile.id,
            settings=settings,
        )
        recent_selected_action_ids, recent_motion_combinations = await _recent_motion_history(
            session,
            user_id=user_id,
            template_id=template.id,
            site_profile=site_profile,
        )
        selection_salt = int(
            sha256_json(
                {
                    "clientTaskId": payload.client_task_id,
                    "storyboards": [
                        {
                            "order": item.order,
                            "sha256": by_id[item.upload_id].sha256,
                        }
                        for item in ordered_inputs
                    ],
                }
            )[:12],
            16,
        )
        plans = [
            compile_video_plan(
                PromptCompileInput(
                    template=template,
                    storyboard_count=len(ordered_inputs),
                    result_count=payload.result_count,
                    action_intensity=payload.action_intensity,
                    camera_tempo="standard",
                    cut_mode=payload.cut_mode,
                    variant_index=index,
                    duration_seconds=payload.duration_seconds,
                    site_profile=site_profile,
                    action_context=validation.action_context,
                    shot_action_contexts=tuple(
                        ActionContext(
                            confirmed=material.confirmed_action_capabilities,
                            denied=material.denied_action_capabilities,
                        )
                        for material in descriptors
                    ),
                    shot_evidence_sources=tuple(observed.source for observed in observations),
                    shot_face_evidence=tuple(observed.face_evidence for observed in observations),
                    garment_material=garment_material,
                    selection_salt=selection_salt,
                    action_library=actions,
                    prompt_budget=MAX_KLING_PROMPT_CHARS - len(role_prompt) - 1,
                    learning_guidance=learning.prompt_guidance,
                    creative_brief=payload.creative_brief.strip(),
                    learning_preferred_action_ids=learning.preferred_action_ids,
                    learning_avoided_action_ids=learning.avoided_action_ids,
                    learning_profile_version=learning.profile_version,
                    learning_evidence_count=learning.evidence_count,
                    recent_selected_action_ids=recent_selected_action_ids,
                    recent_motion_combinations=recent_motion_combinations,
                )
            )
            for index in range(1, payload.result_count + 1)
        ]
        prompt_texts = [
            _validate_final_prompt_length(
                f"{role_prompt} {plan.master_prompt}",
                code="COMPILED_PROMPT_TOO_LONG",
                message="自动提示词加素材角色说明后不能超过 3072 个字符",
            )
            for plan in plans
        ]
        variant_seeds = [plan.variant_seed for plan in plans]
        plan_json = {
            "compiler": AUTOMATIC_PROMPT_COMPILER_VERSION,
            "variants": [
                {**asdict(plan), "master_prompt": prompt_text}
                for plan, prompt_text in zip(plans, prompt_texts, strict=True)
            ],
        }
    else:
        custom_prompt = payload.custom_prompt or ""
        final_prompt = f"{custom_prompt} {role_prompt}"
        final_prompt = _validate_final_prompt_length(
            final_prompt,
            code="CUSTOM_PROMPT_TOO_LONG",
            message="自定义提示词加素材角色说明后不能超过 3072 个字符",
        )
        prompt_texts = [final_prompt for _ in range(payload.result_count)]
        variant_seeds = [
            {"variantIndex": index, "mode": "custom", "promptMutated": False}
            for index in range(1, payload.result_count + 1)
        ]
        plan_json = {
            "compiler": CUSTOM_PROMPT_COMPILER_VERSION,
            "userPrompt": custom_prompt,
            "referenceRolePrompt": role_prompt,
            "finalPrompt": final_prompt,
            "resultCount": payload.result_count,
        }
    if existing_task is not None:
        # Cancellation or stale recovery must win before a worker can reserve paid
        # generation. This conditional UPDATE is the hand-off's atomic gate.
        transitioned = await session.execute(
            update(ParentTask).where(
                ParentTask.id == existing_task.id,
                ParentTask.phase == ParentTaskPhase.ANALYZING,
                ParentTask.analysis_run_id == (
                    expected_analysis_run_id or existing_task.analysis_run_id
                ),
                ParentTask.analysis_stage == "RUNNING",
            ).values(phase=ParentTaskPhase.PREPARING)
        )
        if transitioned.rowcount != 1:
            raise FixedVideoTaskValidationError(
                "AI_CREATIVE_CANCELLED", "任务在编排期间已取消或被恢复；不会提交付费生成"
            )
    task_id = existing_task.id if existing_task is not None else uuid.uuid4()
    consent_at = datetime.now(UTC)
    settings_json = {
        "generationProvider": settings.active_kling_provider_name,
        "generationMode": payload.generation_mode,
        "directorMode": payload.director_mode,
        "videoType": payload.video_type if payload.generation_mode == "automatic" else "ootd",
        "elementIds": list(payload.element_ids),
        "resultCount": payload.result_count,
        "actionIntensity": payload.action_intensity,
        **({"ootdCreativeControls": ootd_controls} if ootd_controls else {}),
        "cutMode": payload.cut_mode,
        "autoSubmitToCutflow": payload.auto_submit_to_cutflow,
        "durationSeconds": payload.duration_seconds,
        "aspectRatio": template.ratio,
        "sound": False if ootd_enabled else template.sound,
        **({"audio": "off"} if ootd_enabled else {}),
        "subtitles": False if ootd_enabled else template.subtitles,
        "smartStoryboard": True if (ootd_enabled or native_i or native_v34) else (
            template.smart_storyboard
        ),
        "multiShot": True if (ootd_enabled or native_i or native_v34) else (
            template.smart_storyboard
        ),
        **({"directorNativeProfile": (OOTD_PROFILE if payload.director_mode == "cross_scene_v2" else "allwe-ootd-sol-simple-v1")} if ootd_enabled
           else {"directorNativeProfile": V34_PROFILE} if native_v34
           else {"directorNativeProfile": "allwe-kling-i-native-five-shot-v1"}
           if native_i else {}),
    }
    # Trace the creative pacing goal without modifying CUT's wire contract.
    if payload.generation_mode == "automatic" and not (native_v34 or ootd_enabled):
        from libtv_agent.services.fastcut_pacing import (
            PROFILE as FASTCUT_PROFILE,
        )
        from libtv_agent.services.fastcut_pacing import (
            eligible as fastcut_eligible,
        )
        if fastcut_eligible(
            enabled=settings.fastcut_ugc_pacing_v1_enabled,
            duration_seconds=payload.duration_seconds, cut_mode=payload.cut_mode,
        ):
            settings_json["creativePacingProfile"] = FASTCUT_PROFILE
    asset_hash = ordered_assets_hash(
        [
            {"id": str(item.upload_id), "order": item.order, "sha256": by_id[item.upload_id].sha256}
            for item in ordered_inputs
        ]
    )
    brief = payload.creative_brief.strip()
    if brief:
        plan_json["creativeBrief"] = brief
        if payload.generation_mode != "automatic":
            prompt_texts = [
                _validate_final_prompt_length(
                    f"{prompt} 补充要求：{brief}",
                    code="COMPILED_PROMPT_TOO_LONG",
                    message="补充要求与完整提示词合计不能超过 3072 个字符，请缩短后重试",
                )
                for prompt in prompt_texts
            ]
            plan_json["finalPrompt"] = prompt_texts[0]
    task = ParentTask(
        id=task_id,
        user_id=user_id,
        client_task_id=payload.client_task_id,
        title=payload.title,
        asset_count=len(upload_ids),
        phase=ParentTaskPhase.PREPARING,
        phase_reason_code=None,
        workflow_kind="FIXED_TEMPLATE",
        template_id=template.id,
        template_version=template.version,
        site_profile_id=source_site["profileId"] if source_site else None,
        site_profile_version=source_site["profileVersion"] if source_site else None,
        settings_json={**settings_json, **({"cutflowSite": source_site} if source_site else {})},
        compiled_plan_json=plan_json,
        material_validation_json=validation_json,
        paid_submission_consent_at=consent_at,
        source_upload_duration_seconds=payload.source_upload_duration_seconds,
        user_requirement_raw="\n".join(filter(None, (payload.custom_prompt, brief))),
        user_requirement_normalized="\n".join(filter(None, (payload.custom_prompt, brief))),
    )
    if existing_task is not None:
        for field in (
            "title", "asset_count", "phase", "phase_reason_code", "workflow_kind",
            "template_id", "template_version", "site_profile_id", "site_profile_version",
            "settings_json", "compiled_plan_json", "material_validation_json",
            "paid_submission_consent_at", "source_upload_duration_seconds",
            "user_requirement_raw", "user_requirement_normalized",
        ):
            setattr(existing_task, field, getattr(task, field))
        existing_task.analysis_stage = "COMPLETED"
        existing_task.analysis_heartbeat_at = datetime.now(UTC)
        existing_task.state_version += 1
        task = existing_task
    task.assets = []
    for item in ordered_inputs:
        upload = by_id[item.upload_id]
        task.assets.append(
            Asset(
                parent_task_id=task_id,
                order_no=item.order,
                required=item.required,
                original_filename=upload.original_filename,
                sha256=upload.sha256,
                mime_type=upload.mime_type,
                size_bytes=upload.size_bytes,
                width=upload.width,
                height=upload.height,
                object_key=upload.object_key,
                thumbnail_key=upload.thumbnail_key,
                source_image_output_id=upload.source_image_output_id,
                used_for_analysis=False,
                used_for_generation=True,
            )
        )
        upload.claimed_task_id = task_id
    for item in ordered_subject_inputs:
        upload = by_id[item.upload_id]
        task.assets.append(
            Asset(
                parent_task_id=task_id,
                source_type="SUBJECT_REFERENCE",
                role="PRODUCT",
                order_no=100 + item.order,
                required=False,
                original_filename=upload.original_filename,
                sha256=upload.sha256,
                mime_type=upload.mime_type,
                size_bytes=upload.size_bytes,
                width=upload.width,
                height=upload.height,
                object_key=upload.object_key,
                thumbnail_key=upload.thumbnail_key,
                source_image_output_id=upload.source_image_output_id,
                used_for_analysis=False,
                used_for_generation=True,
            )
        )
        upload.claimed_task_id = task_id
    task.variants = [
        GenerationVariant(
            variant_type=VariantKind.STABLE,
            revision=index,
            variant_index=index,
            variant_key=f"result-{index}",
            variant_seed_json=variant_seed,
            prompt_text=prompt_text,
            prompt_hash=sha256_json(prompt_text),
            prompt_template_version=(
                OOTD_PROFILE if ootd_enabled else V25_EXPERIMENTAL_VERSION
                if task_uses_v25
                else CREATIVE_PLANNER_VERSION
                if payload.generation_mode == "automatic" and settings.ai_adapter_mode == "openai"
                else AUTOMATIC_PROMPT_COMPILER_VERSION
                if payload.generation_mode == "automatic"
                else CUSTOM_PROMPT_COMPILER_VERSION
            ),
            generation_config_json={**settings_json, "variantIndex": index},
            generation_config_hash=sha256_json({**settings_json, "variantIndex": index}),
            ordered_storyboard_assets_hash=asset_hash,
            confirmed_at=consent_at,
        )
        for index, (variant_seed, prompt_text) in enumerate(
            zip(variant_seeds, prompt_texts, strict=True),
            start=1,
        )
    ]
    session.add(task)
    if ordered_subject_inputs:
        first_subject_upload = by_id[ordered_subject_inputs[0].upload_id]
        subject_name, subject_description = automatic_element_metadata(
            first_subject_upload.original_filename,
            task_id=task_id,
            reference_count=len(ordered_subject_inputs),
        )
        session.add(
            SubjectElementJob(
                parent_task_id=task_id,
                user_id=user_id,
                external_task_id=f"fixed-video-subject-{task_id.hex}",
                name=subject_name,
                description=subject_description,
                status="PENDING",
            )
        )
    session.add(
        AuditEvent(
            actor_id=user_id,
            action="CREATE_FIXED_VIDEO_TASK",
            entity_type="parent_task",
            entity_id=str(task.id),
            from_state=None,
            to_state=ParentTaskPhase.PREPARING.value,
            reason="explicit paid Kling submission consent recorded",
            correlation_id=str(uuid.uuid4()),
            evidence_refs_json=[],
        )
    )
    await session.commit()
    task = await get_task(session, user_id, task.id)

    return await queue_video_task(
        session,
        task=task,
        user_id=user_id,
        settings=settings,
        kling_adapter=kling_adapter,
        object_store=object_store,
    )


async def queue_video_task(
    session: AsyncSession,
    *,
    task: ParentTask,
    user_id: uuid.UUID,
    settings: Settings,
    kling_adapter: VideoProviderAdapter,
    object_store: ObjectStore,
) -> ParentTask:
    """Use the same safety, budget, outbox and runner contract for all direct video tasks."""
    consent_at = task.paid_submission_consent_at or datetime.now(UTC)
    try:
        original_provider = (task.settings_json or {}).get("generationProvider")
        if original_provider and original_provider != kling_adapter.get_provider_name():
            raise RuntimeControlBlockedError(
                "PROVIDER_ROUTE_CHANGED",
                "Video provider changed after task creation; review before paid submission",
            )
        if (
            task.workflow_kind == "DEEP_VIDEO"
            and settings.app_env != "test"
            and not paid_generation_authorized(settings)
        ):
            raise RuntimeControlBlockedError(
                "PAID_GENERATION_BLOCKED",
                "本站尚未授权真实付费生成",
            )
        await ensure_provider_submission_allowed(
            session,
            settings,
            kling_adapter.get_provider_name(),
            user_id=user_id,
            workflow_kind=task.workflow_kind,
            provider_model=(
                task.settings_json.get("model")
                if task.workflow_kind == "DEEP_VIDEO"
                else settings.active_kling_model
            ),
        )
        # Standard paid tasks are subject to USD limits, not Pilot allow-listing.
        attempts = await prepare_kling_task(
            session,
            kling_adapter,
            object_store,
            task,
            settings,
        )
    except (RuntimeControlBlockedError, KlingCliError) as exc:
        return await _mark_action_required(
            session,
            task,
            actor_id=user_id,
            code=exc.code,
            message=str(exc),
        )

    previews: list[dict[str, Any]] = []
    persisted_task_id = task.id
    try:
        for attempt in attempts:
            variant = next(item for item in task.variants if item.id == attempt.variant_id)
            duration_seconds = int(
                variant.generation_config_json.get("durationSeconds")
                or variant.generation_config_json.get("duration")
                or settings.active_kling_duration
            )
            paid_boundary = _paid_boundary(attempt.provider, settings)
            preview = {
                "paidBoundary": paid_boundary,
                "provider": attempt.provider,
                "model": attempt.provider_model,
                "variantIndex": variant.variant_index,
                "promptHash": variant.prompt_hash,
                "estimatedGenerationCount": (
                    2 if variant.generation_config_json.get("deepVideoPipeline")
                    == "depth-first-v1" else 1
                ),
                "costRisk": (
                    f"Submitting through {attempt.provider} consumes video generation credits."
                ),
            }
            previews.append(preview)
            cli_credits = estimate_cli_credits(
                attempt.provider, attempt.provider_model, duration_seconds,
            )
            await reserve_points(
                session,
                user_id=user_id,
                attempt_id=attempt.id,
                estimated_points=(
                    cli_credits if cli_credits is not None else settings.generation_estimated_points
                ),
                daily_limit=settings.daily_user_points_limit,
                # Account budgets use target seconds; provider billing metadata is informational.
                usd_units=duration_seconds,
                usd_amount_override_microusd=(
                    credits_to_microusd(cli_credits) if cli_credits is not None else None
                ),
                usd_pricing_category=(
                    "KLING_CLI" if cli_credits is not None else
                    "RECREATE_DEPTH_VIDEO" if (
                    variant.generation_config_json.get("deepVideoPipeline") == "depth-first-v1"
                ) else {
                    "depth": "DEPTH_VIDEO",
                    "recreate": "RECREATE_VIDEO",
                }.get(variant.generation_config_json.get("deepVideoMode"), "VIDEO")
                ),
            )
            attempt.status = AttemptStatus.DISPATCH_PENDING
            attempt.state_version += 1
            session.add(
                GenerationCommand(
                    attempt_id=attempt.id,
                    status=CommandStatus.READY,
                    command_manifest_json={
                        "provider": "kling",
                        "intentHash": attempt.intent_hash,
                        "variantIndex": variant.variant_index,
                        "createdAt": consent_at.isoformat(),
                        "paidBoundaryPreview": preview,
                    },
                )
            )
        # Reserve video cost first so a downstream CUT-only quote or budget
        # problem cannot stop independently affordable original generation.
        if (task.workflow_kind == "FIXED_TEMPLATE"
                and (task.settings_json or {}).get("autoSubmitToCutflow") is True
                and settings.cutflow_auto_edit_enabled):
            if not attempts or not 1 <= len(task.variants) <= 8:
                task.settings_json = {
                    **(task.settings_json or {}),
                    "autoCutflowPrepayWarning": "CUTFLOW_SOURCE_COUNT_INVALID",
                }
            else:
                try:
                    await reserve_auto_cutflow(
                        session, parent=task, attempt_id=attempts[0].id,
                        units=len(task.variants),
                    )
                except HTTPException as exc:
                    if isinstance(exc.detail, dict) and exc.detail.get("code") in {
                        "USD_DAILY_BUDGET_EXCEEDED", "USD_PRICE_REQUIRED",
                    }:
                        # The edit remains opt-in, but must pass its own USD
                        # admission before the auto worker creates a CUT job.
                        task.settings_json = {
                            **(task.settings_json or {}),
                            "autoCutflowPrepayWarning": exc.detail["code"],
                        }
                    else:
                        raise
    except HTTPException as exc:
        detail = (
            exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
        )
        await session.rollback()
        task = await get_task(session, user_id, persisted_task_id)
        return await _mark_action_required(
            session,
            task,
            actor_id=user_id,
            code=str(detail.get("code", "VIDEO_SUBMISSION_REJECTED")),
            message=str(detail.get("message", str(exc))),
        )

    preview_path = settings.runner_root / "previews" / f"{task.id}.json"
    write_preview_file(
        preview_path,
        {
            "taskId": str(task.id),
            "workflowKind": task.workflow_kind,
            "paidGenerationBoundary": _paid_boundary(
                kling_adapter.get_provider_name(),
                settings,
            ),
            "commands": previews,
        },
    )
    task.phase = ParentTaskPhase.QUEUED
    task.phase_reason_code = None
    task.state_version += 1
    session.add_all(
        [
            AuditEvent(
                actor_id=user_id,
                action="QUEUE_FIXED_VIDEO_TASK",
                entity_type="parent_task",
                entity_id=str(task.id),
                from_state=ParentTaskPhase.PREPARING.value,
                to_state=ParentTaskPhase.QUEUED.value,
                reason="deterministic plans passed Kling preflight and were explicitly submitted",
                correlation_id=str(uuid.uuid4()),
                evidence_refs_json=[str(preview_path)],
            ),
            OutboxEvent(
                aggregate_type="parent_task",
                aggregate_id=str(task.id),
                event_type="GENERATION_COMMANDS_READY",
                payload_json={"taskId": str(task.id), "workflowKind": task.workflow_kind},
            ),
        ]
    )
    await session.commit()
    return await get_task(session, user_id, task.id)
