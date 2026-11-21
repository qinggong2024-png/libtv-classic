"""Durable, non-paid acceptance of automatic fixed-video submissions.

The HTTP handler creates the task and claims uploads in a short transaction.
Only the runner compiles the creative plan and prepares generation commands.
A claimed task may be replayed only before any paid-dispatch artifacts exist,
and only after its lease has expired or the worker has stopped.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.video_provider import VideoProviderAdapter
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import GenerationVariant, ParentTask, StagedUpload
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services.fixed_video_tasks import (
    FixedVideoTaskValidationError,
    create_fixed_video_task,
)
from libtv_agent.services.tasks import task_to_read
from libtv_agent.services.video_type_release import (
    UNBOXING_PAUSED_CODE,
    UNBOXING_PAUSED_MESSAGE,
    unboxing_submission_paused,
)
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
PAYLOAD_KEY = "_pendingAutomaticVideoSubmission"
MAX_AUTOMATIC_PLAN_ATTEMPTS = 2


def _no_generation_variants():
    """A durable pre-paid boundary: no variant/attempt/receipt can exist yet."""
    return ~exists(select(GenerationVariant.id).where(
        GenerationVariant.parent_task_id == ParentTask.id,
    ))


def _stale_plan_condition(cutoff: datetime):
    return or_(
        ParentTask.analysis_heartbeat_at < cutoff,
        and_(
            ParentTask.analysis_heartbeat_at.is_(None),
            ParentTask.analysis_started_at < cutoff,
        ),
    )


async def heartbeat_automatic_video_plan(
    database: Database, task_id: uuid.UUID, run_id: uuid.UUID,
    *, interval_seconds: float = 20.0,
) -> None:
    """Renew an owned Sol planning lease without holding a long DB transaction."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with database.session_factory() as session:
                updated = await session.execute(
                    update(ParentTask).where(
                        ParentTask.id == task_id,
                        ParentTask.phase == ParentTaskPhase.ANALYZING,
                        ParentTask.analysis_stage == "RUNNING",
                        ParentTask.analysis_run_id == run_id,
                    ).values(analysis_heartbeat_at=datetime.now(UTC))
                )
                await session.commit()
                if updated.rowcount != 1:
                    return
        except Exception:
            # A short transient database lock should not kill a live Sol run.
            logger.exception("automatic plan heartbeat failed")


async def _settle_interrupted_plan(
    session: AsyncSession, task: ParentTask, *, cutoff: datetime | None = None,
) -> bool:
    """CAS requeue only a demonstrably pre-paid plan, otherwise require review."""
    predicate = [
        ParentTask.id == task.id,
        ParentTask.workflow_kind == "FIXED_TEMPLATE",
        ParentTask.phase == ParentTaskPhase.ANALYZING,
        ParentTask.analysis_stage == "RUNNING",
        ParentTask.analysis_run_id == task.analysis_run_id,
    ]
    if cutoff is not None:
        predicate.append(_stale_plan_condition(cutoff))
    resumable = (
        task.analysis_run_id is not None
        and task.analysis_attempt_count < MAX_AUTOMATIC_PLAN_ATTEMPTS
        and isinstance((task.settings_json or {}).get(PAYLOAD_KEY), dict)
    )
    if resumable:
        changed = await session.execute(
            update(ParentTask)
            .execution_options(synchronize_session=False)
            .where(*predicate, _no_generation_variants())
            .values(
                phase_reason_code=None,
                analysis_stage="QUEUED",
                analysis_run_id=None,
                analysis_started_at=None,
                analysis_heartbeat_at=None,
                state_version=ParentTask.state_version + 1,
            )
        )
        if changed.rowcount == 1:
            return True
    # Never interpret a missing cloud ID as proof of no charge. Keep anything
    # past the planning boundary out of replay, even if state is inconsistent.
    changed = await session.execute(
        update(ParentTask)
        .execution_options(synchronize_session=False)
        .where(*predicate)
        .values(
            phase=ParentTaskPhase.ACTION_REQUIRED,
            phase_reason_code="AI_CREATIVE_INTERRUPTED",
            analysis_stage="FAILED",
            analysis_heartbeat_at=datetime.now(UTC),
            state_version=ParentTask.state_version + 1,
        )
    )
    return changed.rowcount == 1


async def release_stopped_automatic_plan(
    session: AsyncSession, *, task_id: uuid.UUID, run_id: uuid.UUID,
) -> bool:
    """After cancelling and joining a worker, release only that worker's lease."""
    task = await session.get(ParentTask, task_id)
    if task is None or task.analysis_run_id != run_id:
        return False
    changed = await _settle_interrupted_plan(session, task)
    if changed:
        await session.commit()
    return changed


async def accept_automatic_video_submission(
    session: AsyncSession, *, user_id: uuid.UUID, payload: VideoTaskCreate,
    settings: Settings,
):
    """Return a real task ID before AI planning. Never call a paid provider here."""
    if unboxing_submission_paused(payload.video_type):
        raise FixedVideoTaskValidationError(UNBOXING_PAUSED_CODE, UNBOXING_PAUSED_MESSAGE)
    if settings.ai_adapter_mode == "openai" and not settings.openai_api_key:
        raise FixedVideoTaskValidationError(
            "AI_CREATIVE_NOT_CONFIGURED",
            "AI编排服务未加载模型密钥；素材未占用，未提交视频生成。",
        )
    if not payload.paid_submission_consent:
        raise FixedVideoTaskValidationError(
            "PAID_SUBMISSION_CONSENT_REQUIRED", "必须明确同意付费生成"
        )
    # The asynchronous route must reject unsupported CLI inputs before claiming uploads.
    if settings.kling_provider_backend == "cli" and payload.element_ids:
        raise FixedVideoTaskValidationError(
            "KLING_ELEMENTS_UNSUPPORTED",
            "当前 Kling CLI 生成通道尚不支持历史 Element 绑定，请上传参考图片。",
        )
    ordered = sorted([*payload.storyboards, *payload.subject_images], key=lambda x: x.order)
    ids = [item.upload_id for item in ordered]
    if not ids or len(ids) != len(set(ids)):
        raise FixedVideoTaskValidationError("STORYBOARD_UPLOAD_UNAVAILABLE", "素材为空或重复")
    staged = list(await session.scalars(select(StagedUpload).where(
        StagedUpload.id.in_(ids),
        StagedUpload.user_id == user_id,
        StagedUpload.claimed_task_id.is_(None),
    )))
    if len(staged) != len(ids):
        raise FixedVideoTaskValidationError(
            "STORYBOARD_UPLOAD_UNAVAILABLE", "素材已失效、被使用或不属于当前账号"
        )
    brief = "\n".join(filter(None, (payload.custom_prompt, payload.creative_brief)))
    task = ParentTask(
        id=uuid.uuid4(),
        user_id=user_id,
        client_task_id=payload.client_task_id,
        title=payload.title,
        asset_count=len(ids),
        phase=ParentTaskPhase.ANALYZING,
        analysis_stage="QUEUED",
        workflow_kind="FIXED_TEMPLATE",
        template_id=payload.template_id,
        site_profile_id=payload.site_profile_id,
        settings_json={
            "generationMode": payload.generation_mode,
            "directorMode": payload.director_mode,
            "videoType": payload.video_type if payload.generation_mode == "automatic" else "ootd",
            "generationProvider": settings.active_kling_provider_name,
            "analysisModel": settings.openai_model,
            "resultCount": payload.result_count,
            "durationSeconds": payload.duration_seconds,
            PAYLOAD_KEY: payload.model_dump(mode="json", by_alias=True),
        },
        source_upload_duration_seconds=payload.source_upload_duration_seconds,
        user_requirement_raw=brief,
        user_requirement_normalized=brief,
    )
    # An unsaved ParentTask has unloaded selectin relationships; initialize them
    # explicitly so task_to_read does not attempt async lazy loading.
    task.assets = []
    task.variants = []
    session.add(task)
    for image in staged:
        image.claimed_task_id = task.id
    await session.flush()
    # The caller stores the idempotency result and commits this acceptance atomically.
    return await task_to_read(session, task, settings.daily_user_points_limit)


async def pending_automatic_video_ids(session: AsyncSession, limit: int = 2) -> list[uuid.UUID]:
    return list(await session.scalars(
        select(ParentTask.id).where(
            ParentTask.workflow_kind == "FIXED_TEMPLATE",
            ParentTask.phase == ParentTaskPhase.ANALYZING,
            ParentTask.analysis_stage == "QUEUED",
        ).order_by(ParentTask.created_at).limit(limit)
    ))


async def process_automatic_video_submission(
    database: Database, settings: Settings, kling_adapter: VideoProviderAdapter,
    object_store: ObjectStore, task_id: uuid.UUID,
    *, run_id: uuid.UUID | None = None,
) -> None:
    """Claim a lease and run in an independent session; never block polling."""
    run_id = run_id or uuid.uuid4()
    async with database.session_factory() as session:
        result = await session.execute(
            update(ParentTask).where(
                ParentTask.id == task_id,
                ParentTask.phase == ParentTaskPhase.ANALYZING,
                ParentTask.analysis_stage == "QUEUED",
            ).values(
                analysis_stage="RUNNING",
                analysis_run_id=run_id,
                analysis_started_at=datetime.now(UTC),
                analysis_heartbeat_at=datetime.now(UTC),
                analysis_attempt_count=ParentTask.analysis_attempt_count + 1,
                state_version=ParentTask.state_version + 1,
            )
        )
        await session.commit()
        if result.rowcount != 1:
            return
    heartbeat = asyncio.create_task(
        heartbeat_automatic_video_plan(database, task_id, run_id)
    )
    try:
        async with database.session_factory() as session:
            task = await session.get(ParentTask, task_id)
            if task is None:
                return
            original_provider = (task.settings_json or {}).get("generationProvider")
            if original_provider and original_provider != kling_adapter.get_provider_name():
                raise FixedVideoTaskValidationError(
                    "PROVIDER_ROUTE_CHANGED",
                    "Video provider changed while AI planning was pending; "
                    "no paid generation submitted",
                )
            raw = (task.settings_json or {}).get(PAYLOAD_KEY)
            if not isinstance(raw, dict):
                raise RuntimeError("durable automatic-video request payload missing")
            payload = VideoTaskCreate.model_validate(raw)
            await create_fixed_video_task(
                session, user_id=task.user_id, payload=payload,
                settings=settings, kling_adapter=kling_adapter,
                object_store=object_store, existing_task=task,
                expected_analysis_run_id=run_id,
            )
    except Exception as exc:
        # Never retry a potentially accepted paid request automatically.
        # If the worker already reached PREPARING/QUEUED this path must not overwrite it.
        code = (
            exc.code if isinstance(exc, FixedVideoTaskValidationError)
            else "AI_CREATIVE_WORKER_FAILED"
        )
        # Persist only bounded, trusted rule identifiers. Never persist provider
        # responses, creative prompts, exception messages, or reference images.
        ai_failure_record = None
        if code in {"AI_CREATIVE_PLAN_INVALID", "AI_CREATIVE_COMPILE_FAILED"} and isinstance(
            exc, FixedVideoTaskValidationError
        ):
            raw_rule = exc.details.get("aiPlannerValidationRule")
            rule = (
                raw_rule if isinstance(raw_rule, str) and 3 <= len(raw_rule) <= 64
                and raw_rule.replace("_", "").isalpha() and raw_rule.isupper()
                else "UNCLASSIFIED_VALIDATION"
            )
            raw_attempts = exc.details.get("aiPlannerRequestAttempts")
            attempts = (
                raw_attempts if type(raw_attempts) is int and 0 <= raw_attempts <= 5
                else None
            )
            ai_failure_record = {
                "valid": True, "errors": [], "warnings": [],
                "aiCreativePlanner": {
                    "status": "REJECTED", "validationRule": rule,
                    "requestAttempts": attempts,
                },
            }
        logger.exception(
            "automatic video preparation failed: code=%s validationRule=%s",
            code,
            (ai_failure_record or {}).get("aiCreativePlanner", {}).get("validationRule"),
        )
        async with database.session_factory() as session:
            await session.execute(
                update(ParentTask).where(
                    ParentTask.id == task_id,
                    ParentTask.phase == ParentTaskPhase.ANALYZING,
                    ParentTask.analysis_run_id == run_id,
                ).values(
                    phase=ParentTaskPhase.ACTION_REQUIRED,
                    phase_reason_code=code,
                    analysis_stage="FAILED",
                    analysis_heartbeat_at=datetime.now(UTC),
                    **({"material_validation_json": ai_failure_record}
                       if ai_failure_record is not None else {}),
                    state_version=ParentTask.state_version + 1,
                )
            )
            await session.commit()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def mark_interrupted_automatic_plans(
    session: AsyncSession, *, older_than_minutes: int = 10,
    exclude_ids: tuple[uuid.UUID, ...] = (),
) -> int:
    """Recover abandoned pre-paid Sol plans; never replay an uncertain paid job."""
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    candidates = list(await session.scalars(
        select(ParentTask).where(
            ParentTask.workflow_kind == "FIXED_TEMPLATE",
            ParentTask.phase == ParentTaskPhase.ANALYZING,
            ParentTask.analysis_stage == "RUNNING",
            _stale_plan_condition(cutoff),
            ParentTask.id.not_in(exclude_ids),
        ).order_by(ParentTask.analysis_started_at).limit(50)
    ))
    changed = 0
    for task in candidates:
        changed += int(await _settle_interrupted_plan(session, task, cutoff=cutoff))
    if changed:
        await session.commit()
    return changed
