import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from libtv_agent.api.dependencies import Actor, Session, require_review_write
from libtv_agent.domain.models import (
    CutflowHandoff, CutflowOutputView, GenerationVariant, MultiCutflowTask, ParentTask,
)
from libtv_agent.domain.schemas import ApiModel
from libtv_agent.services import cutflow_observation
from libtv_agent.services.account_access import reserve_usd
from libtv_agent.services.brand_identity import profile_brand_reference
from libtv_agent.services.cutflow_auto_status import auto_dispatch_status
from libtv_agent.services.cutflow_auto_eligibility import eligible_auto_variants
from libtv_agent.services.cutflow_budget import bind_prepaid_cutflow
from libtv_agent.services.cutflow_downloads import download_filename
from libtv_agent.services.cutflow_handoff import (
    CutflowHandoffError,
    configured,
    create_cutflow_handoff,
    lan_receipt_accepted,
)
from libtv_agent.services.cutflow_multi import MultiCutflowError, create_multi_task
from libtv_agent.services.cutflow_multi_worker import multi_worker_ready
from libtv_agent.services.cutflow_policy import AUTO_CONTRACT, LAN_CONTRACT
from libtv_agent.services.cutflow_site import (
    CutflowSiteError,
    bind_missing_task_site,
    site_choices,
)
from sqlalchemy import or_, select, update

router = APIRouter(prefix="/cutflow-handoffs", tags=["cutflow-handoffs"])

class CutflowHandoffCreate(ApiModel):
    task_id: uuid.UUID
    variant_id: uuid.UUID


class CutflowSiteBinding(ApiModel):
    task_id: uuid.UUID
    site_profile_id: str


class MultiCutflowCreate(ApiModel):
    source_ids: list[uuid.UUID]
    idempotency_key: str
    confirm_policy: bool
    allow_single_source: bool = False


@router.get("/sites")
async def source_site_choices(actor: Actor, session: Session):
    profiles, default = await site_choices(session, actor.id)
    return {"defaultProfileId": default, "sites": [
        {"id": profile.id, "version": profile.version,
         "name": profile_brand_reference(profile)["displayName"],
         "siteUrl": profile.site_url} for profile in profiles
    ]}


@router.post("/source-site")
async def bind_source_site(payload: CutflowSiteBinding, actor: Actor, session: Session):
    require_review_write(actor)
    try:
        result = await bind_missing_task_site(
            session, actor.id, payload.task_id, payload.site_profile_id,
        )
    except CutflowSiteError as exc:
        raise HTTPException(409, detail={"code": "CUTFLOW_SITE_REQUIRED",
                                        "message": str(exc)}) from exc
    await session.commit()
    return result


def _read(handoff: CutflowHandoff) -> dict[str, object]:
    return {
        "id": handoff.id,
        "handoffKey": handoff.handoff_key,
        "sourceArtifactId": handoff.artifact_id,
        "status": handoff.status,
        "contractVersion": handoff.contract_version,
        "editMode": ("automatic" if handoff.contract_version in {AUTO_CONTRACT, LAN_CONTRACT}
                     else "manual"),
        "cutflowBatchId": handoff.cutflow_batch_id,
        "cutflowEditorUrl": handoff.cutflow_editor_url,
        "errorCode": handoff.error_code,
        "errorMessage": handoff.error_message,
        "createdAt": handoff.created_at,
        "updatedAt": handoff.updated_at,
        "outputs": [
            {
                "id": item["id"],
                "sha256": item["sha256"],
                "reviewStatus": item["review_status"],
                "durationSeconds": item["duration_seconds"],
                "createdAt": item["created_at"],
                "mediaUrl": f"/api/v1/cutflow-handoffs/{handoff.id}/outputs/{item['id']}",
            }
            for item in handoff.outputs_json or []
        ],
    }


@router.get("")
async def list_handoffs(
    actor: Actor,
    session: Session,
    request: Request,
    variant_id: uuid.UUID | None = None,
) -> dict[str, object]:
    if not configured(request.app.state.settings):
        return {"enabled": False, "handoffs": []}
    query = select(CutflowHandoff).where(CutflowHandoff.user_id == actor.id)
    if variant_id is not None:
        query = query.where(CutflowHandoff.variant_id == variant_id)
    rows = (await session.scalars(query.order_by(CutflowHandoff.created_at.desc()).limit(50))).all()
    return {"enabled": True, "handoffs": [_read(row) for row in rows],
            "siteRequired": request.app.state.settings.cutflow_transport != "lan"}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_handoff(
    payload: CutflowHandoffCreate,
    actor: Actor,
    session: Session,
    request: Request,
) -> dict[str, object]:
    require_review_write(actor)
    try:
        handoff = await create_cutflow_handoff(
            session,
            settings=request.app.state.settings,
            object_store=request.app.state.object_store,
            user_id=actor.id,
            task_id=payload.task_id,
            variant_id=payload.variant_id,
        )
    except CutflowHandoffError as exc:
        code_to_status = {
            "CUTFLOW_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
            "CUTFLOW_HANDOFF_DISABLED": status.HTTP_503_SERVICE_UNAVAILABLE,
            "CUTFLOW_HANDOFF_ROOT_MISSING": status.HTTP_503_SERVICE_UNAVAILABLE,
            "TASK_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "VARIANT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        }
        raise HTTPException(
            code_to_status.get(exc.code, status.HTTP_409_CONFLICT),
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    # 剪辑按「每条原片」计价，与生图生视频共用同一份每日美元预算：
    # 未定价时有预算的账号会被 409 USD_PRICE_REQUIRED 拒绝，超预算会被 429 拒绝，
    # 两者都在提交前发生，且本事务尚未 commit，因此不会留下孤立任务。
    await reserve_usd(
        session, user_id=actor.id, kind="CUTFLOW",
        operation_id=handoff.id, units=1,
    )
    await session.commit()
    return _read(handoff)


async def _cutflow_views(session: Session, actor_id: uuid.UUID, tasks: list[MultiCutflowTask]):
    if not tasks:
        return {}
    rows = (await session.scalars(select(CutflowOutputView).where(
        CutflowOutputView.user_id == actor_id,
        CutflowOutputView.batch_id.in_([task.id for task in tasks]),
    ))).all()
    return {(row.batch_id, row.output_id): row.viewed_at for row in rows}


def _read_multi(task: MultiCutflowTask, viewed: dict | None = None) -> dict[str, object]:
    viewed = viewed or {}
    return {
        "id": str(task.id), "status": task.status,
        "errorCode": task.error_code, "errorMessage": task.error_message,
        "createdAt": task.created_at, "updatedAt": task.updated_at,
        "sources": [{key: item[key] for key in (
            "clipId", "order", "taskId", "variantId", "artifactId", "title",
            "durationSeconds",
        )} for item in task.sources_json],
        "outputs": [
            {
                "id": item["id"], "sha256": item["sha256"],
                "reviewStatus": item["review_status"],
                "durationSeconds": item["duration_seconds"],
                "width": item["width"], "height": item["height"],
                "createdAt": item["created_at"],
                "viewedAt": (viewed[(task.id, str(item['id']))].isoformat() if (task.id, str(item['id'])) in viewed else None),
                "mediaUrl": f"/api/v1/cutflow-handoffs/multi/{task.id}/outputs/{item['id']}",
            }
            for item in task.output_json or []
        ],
    }


@router.post("/multi", status_code=status.HTTP_202_ACCEPTED)
async def create_multi_handoff(
    payload: MultiCutflowCreate, actor: Actor, session: Session, request: Request,
):
    require_review_write(actor)
    # Do not persist an orphan QUEUED task while the CUT multi-source receiver,
    # package publisher, worker and output importer have not been field-verified.
    if not multi_worker_ready(request.app.state.settings):
        raise HTTPException(503, detail={
            "code": "CUTFLOW_MULTI_SOURCE_NOT_READY",
            "message": "统一剪辑通道尚未完成 CUT 端接入和现场联调，未创建任务",
        })
    # A manual takeover of a partially completed auto-edit must use the same
    # parent identity, selection and prepaid hold as the auto worker. This
    # prevents two paid edits from racing for the one surviving source.
    prepaid_parent = None
    if payload.idempotency_key.startswith("auto-unified-v2:"):
        try:
            parent_id = uuid.UUID(payload.idempotency_key.removeprefix("auto-unified-v2:"))
        except ValueError:
            raise HTTPException(409, detail={"code": "INVALID_AUTO_EDIT_IDENTITY"}) from None
        prepaid_parent = await session.scalar(select(ParentTask).where(
            ParentTask.id == parent_id, ParentTask.user_id == actor.id,
        ))
        if prepaid_parent is None or (prepaid_parent.settings_json or {}).get(
            "autoSubmitToCutflow"
        ) is not True:
            raise HTTPException(409, detail={"code": "INVALID_AUTO_EDIT_IDENTITY"})
        candidates = (await session.scalars(select(GenerationVariant).where(
            GenerationVariant.parent_task_id == prepaid_parent.id,
        ).order_by(GenerationVariant.variant_index, GenerationVariant.id))).all()
        selected = eligible_auto_variants(candidates, prepaid_parent.phase)
        if not selected or [item.id for item in selected] != payload.source_ids:
            raise HTTPException(409, detail={"code": "AUTO_EDIT_SOURCES_CHANGED",
                                             "message": "可用原片状态已变化，请刷新后重新送剪"})
    try:
        task = await create_multi_task(
            session,
            actor.id,
            payload.source_ids,
            payload.idempotency_key,
            payload.confirm_policy,
            allow_single_source=payload.allow_single_source,
            include_repeat_reference=request.app.state.settings.cutflow_repeat_reference_enabled,
        )
    except MultiCutflowError as exc:
        await session.rollback()
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc
    # 每条原片计一次剪辑费；同一任务重复提交时 reserve_usd 按 (kind, operation_id) 幂等返回。
    if prepaid_parent is not None:
        await bind_prepaid_cutflow(
            session, parent=prepaid_parent, cutflow_task=task,
            units=len(payload.source_ids),
        )
    else:
        await reserve_usd(
            session, user_id=actor.id, kind="CUTFLOW",
            operation_id=task.id, units=len(payload.source_ids),
        )
    await session.commit()
    return _read_multi(task)


@router.get("/auto-dispatch/{parent_task_id}")
async def read_auto_dispatch(
    parent_task_id: uuid.UUID, actor: Actor, session: Session, request: Request,
):
    """Read-only owner-scoped CUT auto-dispatch diagnosis, no paid submissions."""
    snapshot = await auto_dispatch_status(
        session, request.app.state.settings, actor.id, parent_task_id,
    )
    if snapshot is None:
        raise HTTPException(404, detail="Task not found")
    return snapshot


@router.get("/multi")
async def list_multi_handoffs(actor: Actor, session: Session):
    tasks = (await session.scalars(
        select(MultiCutflowTask)
        .where(MultiCutflowTask.user_id == actor.id)
        .order_by(MultiCutflowTask.created_at.desc(), MultiCutflowTask.id.desc())
        .limit(100)
    )).all()
    viewed = await _cutflow_views(session, actor.id, tasks)
    return {"tasks": [_read_multi(task, viewed) for task in tasks]}


@router.get("/multi/{task_id}")
async def get_multi_handoff(task_id: uuid.UUID, actor: Actor, session: Session):
    task = await session.scalar(select(MultiCutflowTask).where(
        MultiCutflowTask.id == task_id, MultiCutflowTask.user_id == actor.id,
    ))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "multi-source cutflow task was not found")
    viewed = await _cutflow_views(session, actor.id, [task])
    return _read_multi(task, viewed)


@router.post("/multi/{task_id}/outputs/{output_id}/viewed")
async def mark_multi_output_viewed(task_id: uuid.UUID, output_id: str, actor: Actor, session: Session):
    """Playback receipt only: never modifies quality approval or CUT status."""
    if not output_id or len(output_id) > 200:
        raise HTTPException(404, "CUT output not found")
    task = await session.scalar(select(MultiCutflowTask).where(
        MultiCutflowTask.id == task_id, MultiCutflowTask.user_id == actor.id,
    ))
    if task is None or not any(str(item.get("id")) == output_id for item in task.output_json or []):
        raise HTTPException(404, "CUT output not found")
    receipt = await session.get(CutflowOutputView, (actor.id, task_id, output_id))
    if receipt is None:
        receipt = CutflowOutputView(user_id=actor.id, batch_id=task_id, output_id=output_id)
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)
    return {"viewedAt": receipt.viewed_at.isoformat()}


@router.post("/multi/{task_id}/retry")
async def retry_multi_handoff(
    task_id: uuid.UUID,
    actor: Actor,
    session: Session,
    request: Request,
):
    require_review_write(actor)
    if not multi_worker_ready(request.app.state.settings):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "CUTFLOW_MULTI_SOURCE_NOT_READY",
            "message": "统一剪辑通道尚未就绪，未恢复原任务",
            },
        )
    task = await session.scalar(
        select(MultiCutflowTask).where(
            MultiCutflowTask.id == task_id,
            MultiCutflowTask.user_id == actor.id,
        )
    )
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "multi-source cutflow task was not found")
    if task.status != "FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "multi-source cutflow task is not failed")
    if task.error_code == "CUTFLOW_HISTORICAL_TASK_ISOLATED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "CUTFLOW_HISTORICAL_TASK_ISOLATED",
                "message": "该历史剪辑任务已被 CUT 隔离，请重新选择原片创建新的剪辑版本",
            },
        )
    manifest = task.manifest_json or {}
    digest = manifest.get("manifestSha256")
    accepted = (
        isinstance(digest, str)
        and manifest.get("_acceptedManifestSha256") == digest
    )
    resumed_status = (
        "IMPORTING" if accepted else "READY" if task.manifest_path else "QUEUED"
    )
    result = await session.execute(
        update(MultiCutflowTask)
        .where(
            MultiCutflowTask.id == task.id,
            MultiCutflowTask.user_id == actor.id,
            MultiCutflowTask.status == "FAILED",
        )
        .values(
            status=resumed_status,
            error_code=None,
            error_message=None,
            failure_count=0,
            next_run_at=None,
            lease_token=None,
            lease_until=None,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "multi-source cutflow recovery is already running",
        )
    await session.commit()
    await session.refresh(task)
    return _read_multi(task)


@router.get("/multi/{task_id}/outputs/{output_id}")
async def multi_output_media(
    task_id: uuid.UUID,
    output_id: str,
    actor: Actor,
    session: Session,
    request: Request,
    download: bool = False,
) -> FileResponse:
    task = await session.scalar(select(MultiCutflowTask).where(
        MultiCutflowTask.id == task_id,
        MultiCutflowTask.user_id == actor.id,
    ))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "multi-source cutflow task was not found")
    output = next((item for item in task.output_json or [] if item.get("id") == output_id), None)
    if output is None or not isinstance(output.get("object_key"), str):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edited output was not found")
    try:
        local = await request.app.state.object_store.localize(
            output["object_key"],
            request.app.state.settings.artifact_root
            / "cutflow-downloads"
            / f"multi-{task.id}-{output_id}.mp4",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_410_GONE, "edited output has expired") from exc
    return FileResponse(
        local,
        media_type="video/mp4",
        filename=await download_filename(session, task, output) if download else None,
        headers={"Cache-Control": "private, no-store"},
    )


async def _owned(handoff_id: uuid.UUID, actor: Actor, session: Session) -> CutflowHandoff:
    handoff = await session.scalar(
        select(CutflowHandoff).where(
            CutflowHandoff.id == handoff_id,
            CutflowHandoff.user_id == actor.id,
        )
    )
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cutflow handoff was not found")
    return handoff


@router.post("/{handoff_id}/retry")
async def retry_handoff(handoff_id: uuid.UUID, actor: Actor, session: Session, request: Request):
    require_review_write(actor)
    handoff = await _owned(handoff_id, actor, session)
    if handoff.status != "FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "handoff is not failed")
    observation = await asyncio.to_thread(
        cutflow_observation.matching, request.app.state.settings, handoff,
    )
    accepted = handoff.contract_version == LAN_CONTRACT and (
        lan_receipt_accepted(handoff) or handoff.cutflow_batch_id is not None
        or bool(observation.get("remoteStatus"))
    )
    now = datetime.now(UTC)
    result = await session.execute(update(CutflowHandoff).where(
        CutflowHandoff.id == handoff.id, CutflowHandoff.user_id == actor.id,
        CutflowHandoff.status == "FAILED",
        or_(CutflowHandoff.lease_until.is_(None), CutflowHandoff.lease_until < now),
    ).values(
        status="IMPORTING" if accepted else "READY" if handoff.manifest_path else "QUEUED",
        failure_count=0, next_run_at=None, error_code=None, error_message=None,
        lease_token=None, lease_until=None,
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "handoff recovery is already running")
    await session.commit()
    await session.refresh(handoff)
    return _read(handoff)


@router.get("/{handoff_id}/outputs/{output_id}")
async def output_media(
    handoff_id: uuid.UUID,
    output_id: str,
    actor: Actor,
    session: Session,
    request: Request,
    download: bool = False,
) -> FileResponse:
    handoff = await _owned(handoff_id, actor, session)
    output = next((item for item in handoff.outputs_json or [] if item["id"] == output_id), None)
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edited output was not found")
    try:
        local = await request.app.state.object_store.localize(
            output["object_key"],
            request.app.state.settings.artifact_root
            / "cutflow-downloads"
            / f"{handoff.id}-{output_id}.mp4",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_410_GONE, "edited output has expired") from exc
    return FileResponse(local, media_type="video/mp4",
                        filename=await download_filename(session, handoff, output) if download else None,
                        headers={"Cache-Control": "private, no-store"})


@router.get("/{handoff_id}/diagnostics")
async def handoff_diagnostics(
    handoff_id: uuid.UUID, actor: Actor, session: Session, request: Request,
) -> dict:
    handoff = await _owned(handoff_id, actor, session)
    return await asyncio.to_thread(
        cutflow_observation.public_snapshot, request.app.state.settings,
        handoff, datetime.now(UTC),
    )


@router.get("/{handoff_id}")
async def get_handoff(
    handoff_id: uuid.UUID,
    actor: Actor,
    session: Session,
) -> dict[str, object]:
    handoff = await session.scalar(
        select(CutflowHandoff).where(
            CutflowHandoff.id == handoff_id,
            CutflowHandoff.user_id == actor.id,
        )
    )
    if handoff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cutflow handoff was not found")
    return _read(handoff)
