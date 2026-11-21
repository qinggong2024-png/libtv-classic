import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.api.users import (
    AdminUserResponse,
    audit_event,
    get_managed_user,
    require_account_admin,
    revoke_sessions,
    user_response,
)
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import (
    AccountUsage,
    AuditEvent,
    Brand,
    CatalogEntry,
    ImageGenerationBatch,
    ParentTask,
    PromptPreset,
    StagedUpload,
    SubjectElementJob,
    User,
)
from libtv_agent.services.account_access import platform_admin, visible_users
from libtv_agent.services.brand_identity import brand_response
from libtv_agent.services.kling_cli_cost import credits_to_microusd, estimate_cli_credits
from libtv_agent.services.page_access import PagePermission
from libtv_agent.services.points import SHANGHAI, business_date
from libtv_agent.services.runtime_control import get_runtime_config, update_runtime_config
from libtv_agent.services.usage_pricing import PRICE_COLUMNS, PRICING_KEY, global_usage_prices
from libtv_agent.services.usage_reports import aggregate_usage, display_totals, empty_totals
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

router = APIRouter(tags=["accounts"])
Money = Decimal


async def account_report_scope(session, actor, user_id, brand_id, search=None):
    scope = select(User.id).where(User.id.in_(visible_users(actor)))
    # Personal reports never expose a colleague, even to legacy brand administrators.
    if not platform_admin(actor):
        scope = scope.where(User.id == actor.id)
    if user_id is not None:
        scope = scope.where(User.id == user_id)
        if not await session.scalar(scope):
            raise HTTPException(404, "账号不存在")
    if brand_id is not None:
        if not platform_admin(actor) and actor.brand_id != brand_id:
            raise HTTPException(404, "品牌不存在")
        scope = scope.where(User.brand_id == brand_id)
    if search:
        scope = scope.where(User.username.icontains(search, autoescape=True))
    return scope


def require_usage_admin(actor: Actor) -> None:
    require_account_admin(actor)


def account_report_dates(day, start_date, end_date):
    if day is not None and (start_date is not None or end_date is not None):
        raise HTTPException(422, "统计日期和日期范围不能同时指定")
    if (start_date is None) != (end_date is None):
        raise HTTPException(422, "请同时指定开始日期和结束日期")
    start = start_date or day or business_date()
    end = end_date or day or business_date()
    if start > end or start == date.min or end == date.max:
        raise HTTPException(422, "日期范围无效")
    return start, end


class BrandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class AccountPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brandId: uuid.UUID | None = None
    role: UserRole | None = None
    isBrandAdmin: bool | None = None
    isActive: bool | None = None
    dailyBudgetUsd: Money | None = Field(default=None, ge=0, le=1_000_000, decimal_places=6)
    pagePermissions: list[PagePermission] | None = None


class GlobalUsagePricing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    imagePriceUsd: Money | None = Field(ge=0, le=10_000, decimal_places=6)
    videoPricePerSecondUsd: Money | None = Field(ge=0, le=10_000, decimal_places=6)
    depthVideoPricePerSecondUsd: Money | None = Field(ge=0, le=10_000, decimal_places=6)
    recreateVideoPricePerSecondUsd: Money | None = Field(ge=0, le=10_000, decimal_places=6)
    # 每条原片的剪辑预估。留空为未定价：有预算的账号将无法送剪（后端 409 USD_PRICE_REQUIRED）。
    cutflowPriceUsd: Money | None = Field(default=None, ge=0, le=10_000, decimal_places=6)


@router.get("/admin/usage-pricing")
async def read_usage_pricing(actor: Actor, session: Session) -> dict:
    require_account_admin(actor)
    prices, conflicts, version = await global_usage_prices(session)
    return {
        **{key: None if value is None else value / 1_000_000 for key, value in prices.items()},
        "version": version,
        "legacyConflicts": conflicts,
    }


@router.put("/admin/usage-pricing")
async def save_usage_pricing(
    payload: GlobalUsagePricing, actor: Actor, session: Session, request: Request
) -> dict:
    require_account_admin(actor)
    settings = request.app.state.settings
    current = await get_runtime_config(session, settings, create=False)
    if payload.version != (current.version if current else 0):
        raise HTTPException(409, "设置已更新，请刷新全站单价后重新保存")
    current = current or await get_runtime_config(session, settings)
    config = {
        **current.config_json,
        PRICING_KEY: {
            key: None if getattr(payload, key) is None else int(getattr(payload, key) * 1_000_000)
            for key in PRICE_COLUMNS
        },
    }
    try:
        await update_runtime_config(
            session,
            settings,
            config=config,
            actor_id=actor.id,
            reason="调整全站图片与视频预估单价",
            expected_version=current.version,
        )
        await session.commit()
    except (ValueError, IntegrityError) as exc:
        await session.rollback()
        raise HTTPException(409, "设置已更新，请刷新全站单价后重新保存") from exc
    return await read_usage_pricing(actor, session)


@router.get("/admin/brands")
async def list_brands(actor: Actor, session: Session) -> list[dict]:
    require_account_admin(actor)
    query = select(Brand).order_by(Brand.name)
    if not platform_admin(actor):
        query = query.where(Brand.id == actor.brand_id)
    return [brand_response(b) for b in await session.scalars(query)]


@router.post("/admin/brands", status_code=201)
async def create_brand(payload: BrandCreate, actor: Actor, session: Session) -> dict:
    if not platform_admin(actor):
        raise HTTPException(403, "仅平台管理员可以创建品牌")
    brand = Brand(name=payload.name)
    session.add(brand)
    try:
        await session.flush()
        session.add(
            AuditEvent(
                actor_id=actor.id,
                action="CREATE_BRAND",
                entity_type="brand",
                entity_id=str(brand.id),
                reason="管理员创建品牌",
                correlation_id=str(uuid.uuid4()),
                evidence_refs_json=[],
            )
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "品牌名称已存在") from exc
    return brand_response(brand)


@router.patch("/admin/users/{user_id}/policy", response_model=AdminUserResponse)
async def patch_policy(
    user_id: uuid.UUID, payload: AccountPolicy, actor: Actor, session: Session
) -> AdminUserResponse:
    require_account_admin(actor)
    user = await get_managed_user(session, user_id, actor)
    previous_access = (user.role, user.brand_id, user.is_brand_admin, user.is_active)
    permissions_changed = False
    fields = payload.model_fields_set
    if "pagePermissions" in fields:
        if payload.pagePermissions is None:
            raise HTTPException(422, "页面权限不能为空，未授权任何页面请填写空列表")
        permissions = list(dict.fromkeys(payload.pagePermissions))
        permissions_changed = user.page_permissions_json != permissions
        user.page_permissions_json = permissions
    if not platform_admin(actor) and fields & {"brandId", "isBrandAdmin"}:
        raise HTTPException(403, "仅平台管理员可以调整品牌归属和品牌管理员")
    if "role" in fields:
        if payload.role in {None, UserRole.ADMIN}:
            raise HTTPException(422, "请选择成员角色，平台管理员账号受保护")
        user.role = payload.role
    if "brandId" in fields and payload.brandId != user.brand_id:
        if user.brand_id is not None:
            # Never make another brand inherit existing assets, tasks, or private styles.
            for model, owner in (
                (ParentTask, ParentTask.user_id),
                (ImageGenerationBatch, ImageGenerationBatch.user_id),
                (CatalogEntry, CatalogEntry.updated_by),
                (AccountUsage, AccountUsage.user_id),
                (StagedUpload, StagedUpload.user_id),
                (PromptPreset, PromptPreset.user_id),
                (SubjectElementJob, SubjectElementJob.user_id),
            ):
                if await session.scalar(select(model.id).where(owner == user.id).limit(1)):
                    raise HTTPException(409, "账号已有品牌数据，请停用旧账号并创建新品牌账号")
        if payload.brandId is not None and await session.get(Brand, payload.brandId) is None:
            raise HTTPException(404, "品牌不存在")
        user.brand_id = payload.brandId
    if "isBrandAdmin" in fields:
        if payload.isBrandAdmin is None:
            raise HTTPException(422, "品牌管理员设置不能为空")
        user.is_brand_admin = payload.isBrandAdmin
    if user.is_brand_admin and (user.brand_id is None or user.role == UserRole.AUDITOR):
        raise HTTPException(422, "品牌管理员必须归属品牌且不能为只读账号")
    if "isActive" in fields:
        if payload.isActive is None:
            raise HTTPException(422, "账号状态不能为空")
        user.is_active = payload.isActive
    for public, column in (("dailyBudgetUsd", "daily_budget_microusd"),):
        if public in fields:
            value = getattr(payload, public)
            setattr(user, column, None if value is None else int(value * 1_000_000))
    if permissions_changed or previous_access != (
        user.role,
        user.brand_id,
        user.is_brand_admin,
        user.is_active,
    ):
        await revoke_sessions(session, user.id)
    session.add(
        audit_event(
            actor.id,
            "UPDATE_ACCOUNT_POLICY",
            user,
            "调整账号权限与美元预算：" + ",".join(sorted(fields)),
        )
    )
    await session.commit()
    return user_response(user)


@router.get("/account/generation-budget")
async def generation_budget(actor: Actor, session: Session, request: Request) -> dict:
    """Today's own budget only, including for platform and brand administrators."""
    day = business_date()
    amount, unpriced = (
        await session.execute(
            select(
                func.coalesce(func.sum(AccountUsage.amount_microusd), 0),
                func.count(AccountUsage.id) - func.count(AccountUsage.amount_microusd),
            ).where(
                AccountUsage.user_id == actor.id,
                AccountUsage.business_date == day,
                AccountUsage.status != "RELEASED",
            )
        )
    ).one()
    prices, _, _ = await global_usage_prices(session)
    display_prices = {
        key: None if value is None else value / 1_000_000
        for key, value in prices.items()
    }
    settings = request.app.state.settings
    if settings.kling_provider_backend == "cli" and settings.kling_cli_enabled:
        per_second_credits = estimate_cli_credits(
            "kling_official_cli", settings.kling_cli_default_model, 1,
        )
        if per_second_credits is not None:
            display_prices["videoPricePerSecondUsd"] = (
                credits_to_microusd(per_second_credits) / 1_000_000
            )
    return {
        "day": day.isoformat(),
        "currency": "USD",
        "dailyBudgetUsd": None if actor.daily_budget_microusd is None
        else actor.daily_budget_microusd / 1_000_000,
        "budgetUsedUsd": amount / 1_000_000,
        "unpricedCount": unpriced,
        "prices": display_prices,
    }


@router.get("/account/usage")
@router.get("/admin/account-usage", dependencies=[Depends(require_usage_admin)])
async def account_usage(
    actor: Actor,
    session: Session,
    userId: uuid.UUID | None = None,
    brandId: uuid.UUID | None = None,
    day: date | None = None,
    startDate: date | None = None,
    endDate: date | None = None,
    status: Literal["OCCUPIED", "ESTIMATED", "RESERVED", "UNCERTAIN", "RELEASED"] | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    eventOffset: int = Query(default=0, ge=0),
    eventLimit: int = Query(default=50, ge=1, le=100),
    summaryOffset: int = Query(default=0, ge=0),
    summaryLimit: int | None = Query(default=None, ge=1, le=100),
    includeEvents: bool = True,
    search: str | None = Query(default=None, max_length=80),
    kind: Literal["IMAGE", "VIDEO", "CUTFLOW"] | None = None,
    unpricedOnly: bool = False,
) -> dict:
    scope = await account_report_scope(session, actor, userId, brandId, search)
    today = business_date()
    start, end = account_report_dates(day, startDate, endDate)
    criteria = (
        AccountUsage.user_id.in_(scope),
        AccountUsage.business_date >= start,
        AccountUsage.business_date <= end,
    )
    if status == "OCCUPIED":
        criteria += (AccountUsage.status != "RELEASED",)
    elif status is not None:
        criteria += (AccountUsage.status == status,)
    if kind is not None:
        criteria += (AccountUsage.kind == kind,)
    if unpricedOnly:
        criteria += (AccountUsage.amount_microusd.is_(None), AccountUsage.status != "RELEASED")
    entries = list(
        await session.scalars(
            select(AccountUsage)
            .where(*criteria)
            .order_by(AccountUsage.created_at.desc(), AccountUsage.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    total = int(
        await session.scalar(select(func.count()).select_from(AccountUsage).where(*criteria)) or 0
    )
    aggregates = await aggregate_usage(session, scope, start, end)
    daily = aggregates if start == end == today else await aggregate_usage(
        session, scope, today, today
    )
    summary_total = int(await session.scalar(
        select(func.count()).select_from(User).where(User.id.in_(scope))
    ) or 0)
    user_query = select(User).where(User.id.in_(scope)).order_by(User.username, User.id)
    if summaryLimit is not None:
        user_query = user_query.offset(summaryOffset).limit(summaryLimit)
    users = list(await session.scalars(user_query))
    names = dict((await session.execute(
        select(User.id, User.username).where(
            User.id.in_(scope), User.id.in_({entry.user_id for entry in entries})
        )
    )).all())
    totals = empty_totals()
    for values in aggregates.values():
        for key, value in values.items():
            totals[key] += value
    summaries = []
    for user in users:
        values = aggregates.get(user.id, empty_totals())
        current = daily.get(user.id, empty_totals())
        remaining = (
            None if user.daily_budget_microusd is None or current["unpricedCount"]
            else max(0, user.daily_budget_microusd - current["budgetUsedUsd"]) / 1_000_000
        )
        summaries.append({
            "userId": str(user.id),
            "username": user.username,
            "brandId": str(user.brand_id) if user.brand_id else None,
            "isActive": user.is_active,
            **display_totals(values),
            "dailyBudgetUsd": None if user.daily_budget_microusd is None
            else user.daily_budget_microusd / 1_000_000,
            "todayBudgetUsedUsd": current["budgetUsedUsd"] / 1_000_000,
            "todayUnpricedCount": current["unpricedCount"],
            "todayRemainingUsd": remaining,
        })
    policies = [
        {
            "userId": str(u.id),
            "username": u.username,
            "dailyBudgetUsd": None
            if u.daily_budget_microusd is None
            else u.daily_budget_microusd / 1_000_000,
        }
        for u in users
    ]
    event_criteria = (
        AuditEvent.actor_id.in_(scope),
        AuditEvent.created_at >= datetime.combine(
            start, time.min, SHANGHAI
        ).astimezone(UTC),
        AuditEvent.created_at < datetime.combine(
            end + timedelta(days=1), time.min, SHANGHAI
        ).astimezone(UTC),
    )
    event_total = int(await session.scalar(
        select(func.count()).select_from(AuditEvent).where(*event_criteria)
    ) or 0) if includeEvents else 0
    events = list(
        await session.scalars(
            select(AuditEvent)
            .where(*event_criteria)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset(eventOffset)
            .limit(eventLimit)
        )
    ) if includeEvents else []
    event_names = dict((await session.execute(
        select(User.id, User.username).where(
            User.id.in_(scope), User.id.in_({event.actor_id for event in events})
        )
    )).all())
    return {
        "day": end.isoformat(),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "budgetDay": today.isoformat(),
        "currency": "USD",
        "total": total,
        **display_totals(totals),
        "summaries": summaries,
        "summaryTotal": summary_total,
        "policies": policies,
        "eventTotal": event_total,
        "items": [
            {
                "id": str(e.id),
                "username": names.get(e.user_id, ""),
                "kind": e.kind,
                "category": e.pricing_category,
                "operationId": str(e.operation_id),
                "units": e.units,
                "amountUsd": None if e.amount_microusd is None else e.amount_microusd / 1_000_000,
                "status": e.status,
                "createdAt": e.created_at.isoformat(),
            }
            for e in entries
        ],
        "events": [
            {
                "id": str(e.id),
                "username": event_names.get(e.actor_id, ""),
                "action": e.action,
                "createdAt": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/account/events")
async def account_events(
    actor: Actor,
    session: Session,
    userId: uuid.UUID | None = None,
    brandId: uuid.UUID | None = None,
    startDate: date | None = None,
    endDate: date | None = None,
    action: str | None = Query(default=None, min_length=1, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    scope = await account_report_scope(session, actor, userId, brandId)
    start, end = account_report_dates(None, startDate, endDate)
    criteria = (
        AuditEvent.actor_id.in_(scope),
        AuditEvent.created_at >= datetime.combine(start, time.min, SHANGHAI).astimezone(UTC),
        AuditEvent.created_at < datetime.combine(
            end + timedelta(days=1), time.min, SHANGHAI
        ).astimezone(UTC),
    )
    if action is not None:
        criteria += (AuditEvent.action == action,)
    total = int(await session.scalar(
        select(func.count()).select_from(AuditEvent).where(*criteria)
    ) or 0)
    entries = list(await session.scalars(
        select(AuditEvent).where(*criteria)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset(offset).limit(limit)
    ))
    names = dict((await session.execute(
        select(User.id, User.username).where(
            User.id.in_(scope), User.id.in_({entry.actor_id for entry in entries})
        )
    )).all())
    return {
        "startDate": start.isoformat(), "endDate": end.isoformat(), "total": total,
        "items": [{
            "id": str(entry.id), "username": names.get(entry.actor_id, ""),
            "action": entry.action, "createdAt": entry.created_at.isoformat(),
            "entityType": entry.entity_type, "entityId": entry.entity_id,
        } for entry in entries],
    }
