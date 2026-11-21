from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.exc import OperationalError
from libtv_agent.adapters.wavespeed_video import WaveSpeedVideoAdapter
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.domain.schemas import DeepVideoTaskCreate, TaskRead, VideoTaskCreate
from libtv_agent.services.async_video_submission import accept_automatic_video_submission
from libtv_agent.services.deep_video_tasks import create_deep_video_task
from libtv_agent.services.fixed_video_tasks import (
    FixedVideoTaskValidationError,
    create_fixed_video_task,
)
from libtv_agent.services.generation_safety import paid_generation_authorized
from libtv_agent.services.idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    load_idempotent_response,
    release_idempotency_claim,
    store_idempotent_response,
)
from libtv_agent.services.video_type_release import (
    UNBOXING_PAUSED_CODE, UNBOXING_PAUSED_MESSAGE, unboxing_submission_paused,
)
from libtv_agent.services.subject_elements import schedule_subject_element_job
from libtv_agent.services.tasks import task_to_read

router = APIRouter(prefix="/video-tasks", tags=["video-tasks"])


@router.get("/deep/status")
async def deep_video_status(actor: Actor, request: Request) -> dict[str, object]:
    from libtv_agent.api.admin import _refresh_wavespeed_config_from_disk

    _refresh_wavespeed_config_from_disk(request)
    settings = request.app.state.settings
    configured = settings.wavespeed_enabled and bool(settings.effective_wavespeed_api_key)
    enabled = configured and paid_generation_authorized(settings)
    return {
        "configured": configured,
        "enabled": enabled,
        "recreateModel": settings.wavespeed_recreate_video_model,
        "reason": None
        if enabled
        else ("请管理员配置并启用 WaveSpeed API" if not configured else "本站尚未授权真实付费生成"),
    }


@router.post("/deep", response_model=TaskRead, status_code=201)
async def create_deep_task(
    payload: DeepVideoTaskCreate,
    actor: Actor,
    session: Session,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> TaskRead:
    actor_id = actor.id
    operation = "CREATE_DEEP_VIDEO_TASK"
    payload_json = payload.model_dump(mode="json", by_alias=True)
    try:
        cached = await load_idempotent_response(
            session,
            tenant_id=str(actor_id),
            operation_type=operation,
            key=idempotency_key,
            payload=payload_json,
        )
    except (IdempotencyConflictError, IdempotencyInProgressError) as exc:
        raise HTTPException(
            409,
            detail={
                "code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"
                if isinstance(exc, IdempotencyInProgressError)
                else "IDEMPOTENCY_KEY_REUSED",
                "message": str(exc),
            },
        ) from exc
    if cached:
        return TaskRead.model_validate(cached[1])
    try:
        from libtv_agent.api.admin import _refresh_wavespeed_config_from_disk

        _refresh_wavespeed_config_from_disk(request)
        task = await create_deep_video_task(
            session,
            user_id=actor_id,
            payload=payload,
            settings=request.app.state.settings,
            kling_adapter=WaveSpeedVideoAdapter(
                request.app.state.settings,
                client=request.app.state.wavespeed_adapter,
            ),
            object_store=request.app.state.object_store,
        )
    except FixedVideoTaskValidationError as exc:
        await session.rollback()
        await release_idempotency_claim(
            session,
            tenant_id=str(actor_id),
            operation_type=operation,
            key=idempotency_key,
            payload=payload_json,
        )
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    result = await task_to_read(session, task, request.app.state.settings.daily_user_points_limit)
    await store_idempotent_response(
        session,
        tenant_id=str(actor_id),
        operation_type=operation,
        key=idempotency_key,
        payload=payload_json,
        response_status=201,
        response_body=result.model_dump(mode="json", by_alias=True),
    )
    await session.commit()
    return result


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_video_task(
    payload: VideoTaskCreate,
    actor: Actor,
    session: Session,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> TaskRead:
    # Reject frozen modes before idempotency claims, upload claims or paid work.
    if unboxing_submission_paused(payload.video_type):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": UNBOXING_PAUSED_CODE, "message": UNBOXING_PAUSED_MESSAGE},
        )
    actor_id = actor.id
    operation = "CREATE_FIXED_VIDEO_TASK"
    payload_json = payload.model_dump(mode="json", by_alias=True)
    try:
        cached = await load_idempotent_response(
            session,
            tenant_id=str(actor_id),
            operation_type=operation,
            key=idempotency_key,
            payload=payload_json,
        )
    except OperationalError as exc:
        # This failure occurs before the request has accepted any task or
        # reached a paid provider. Do not mislabel it as an unknown charge.
        await session.rollback()
        if "database is locked" in str(exc).lower():
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "SUBMISSION_NOT_ACCEPTED_DB_BUSY",
                    "message": "任务未被接收：数据库繁忙，请稍后使用原素材重试；未提交付费生成。",
                },
            ) from exc
        raise
    except (IdempotencyConflictError, IdempotencyInProgressError) as exc:
        code = (
            "IDEMPOTENCY_REQUEST_IN_PROGRESS"
            if isinstance(exc, IdempotencyInProgressError)
            else "IDEMPOTENCY_KEY_REUSED"
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": code, "message": str(exc)},
        ) from exc
    if cached:
        cached_result = TaskRead.model_validate(cached[1])
        schedule_subject_element_job(
            request.app,
            task_id=cached_result.id,
            user_id=actor_id,
        )
        return cached_result

    # Commit the idempotency receipt before running any slow creation logic.
    # This releases the SQLite writer lock while protecting against paid replays.
    await session.commit()

    try:
        settings = request.app.state.settings
        if payload.generation_mode == "automatic" and settings.ai_adapter_mode == "openai":
            result = await accept_automatic_video_submission(
                session, user_id=actor_id, payload=payload, settings=settings,
            )
            await store_idempotent_response(
                session, tenant_id=str(actor_id), operation_type=operation,
                key=idempotency_key, payload=payload_json,
                response_status=201, response_body=result.model_dump(mode="json", by_alias=True),
            )
            await session.commit()
            return result
        task = await create_fixed_video_task(
            session,
            user_id=actor_id,
            payload=payload,
            settings=settings,
            kling_adapter=request.app.state.kling_adapter,
            object_store=request.app.state.object_store,
        )
    except FixedVideoTaskValidationError as exc:
        await release_idempotency_claim(
            session,
            tenant_id=str(actor_id),
            operation_type=operation,
            key=idempotency_key,
            payload=payload_json,
        )
        upstream_codes = {
            "AI_CREATIVE_GATEWAY_TIMEOUT", "AI_CREATIVE_REQUEST_TIMEOUT",
            "AI_CREATIVE_CONNECTION_FAILED", "AI_CREATIVE_AUTH_FAILED",
            "AI_CREATIVE_RATE_LIMITED", "AI_CREATIVE_ENDPOINT_NOT_FOUND",
            "AI_CREATIVE_REQUEST_REJECTED", "AI_CREATIVE_UPSTREAM_UNAVAILABLE",
            "AI_CREATIVE_RESPONSE_INVALID",
        }
        response_status = (
            status.HTTP_504_GATEWAY_TIMEOUT
            if exc.code in {"AI_CREATIVE_GATEWAY_TIMEOUT", "AI_CREATIVE_REQUEST_TIMEOUT"}
            else status.HTTP_502_BAD_GATEWAY if exc.code in upstream_codes
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            response_status,
            detail={"code": exc.code, "message": str(exc), "details": exc.details},
        ) from exc
    except Exception:
        await release_idempotency_claim(
            session,
            tenant_id=str(actor_id),
            operation_type=operation,
            key=idempotency_key,
            payload=payload_json,
        )
        raise

    result = await task_to_read(
        session,
        task,
        request.app.state.settings.daily_user_points_limit,
    )
    await store_idempotent_response(
        session,
        tenant_id=str(actor_id),
        operation_type=operation,
        key=idempotency_key,
        payload=payload_json,
        response_status=status.HTTP_201_CREATED,
        response_body=result.model_dump(mode="json", by_alias=True),
    )
    await session.commit()
    if payload.subject_images:
        schedule_subject_element_job(
            request.app,
            task_id=task.id,
            user_id=actor_id,
        )
    return result
