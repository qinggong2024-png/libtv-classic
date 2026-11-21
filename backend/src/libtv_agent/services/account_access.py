"""Account scopes and USD estimates shared by API and background workers."""

import uuid
from datetime import datetime

from fastapi import HTTPException
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import AccountUsage, Brand, User
from libtv_agent.services.page_access import management_admin
from libtv_agent.services.points import business_date
from libtv_agent.services.usage_pricing import CATEGORY_PRICES, global_usage_prices
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession


def platform_admin(actor: User) -> bool:
    return management_admin(actor)


def account_manager(actor: User) -> bool:
    return management_admin(actor)


def visible_users(actor: User):
    if platform_admin(actor):
        return select(User.id)
    if actor.brand_id and actor.is_brand_admin:
        return select(User.id).where(User.brand_id == actor.brand_id, User.role != UserRole.ADMIN)
    return select(User.id).where(User.id == actor.id)


def review_users(actor: User):
    if platform_admin(actor):
        return select(User.id)
    if actor.brand_id and (actor.is_brand_admin or actor.role == UserRole.OPERATOR):
        return select(User.id).where(User.brand_id == actor.brand_id, User.role != UserRole.ADMIN)
    return select(User.id).where(User.id == actor.id)


async def reserve_usd(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    operation_id: uuid.UUID,
    units: int,
    pricing_category: str | None = None,
    amount_override_microusd: int | None = None,
    now: datetime | None = None,
) -> AccountUsage:
    if units <= 0:
        raise ValueError("usage units must be positive")
    category = pricing_category or kind
    if (kind not in {"IMAGE", "VIDEO", "CUTFLOW"}
            or category not in {*CATEGORY_PRICES, "RECREATE_DEPTH_VIDEO", "KLING_CLI"}
            or (kind == "IMAGE") != (category == "IMAGE")):
        raise ValueError("invalid usage pricing category")
    if category == "KLING_CLI" and (kind != "VIDEO" or amount_override_microusd is None):
        raise ValueError("Kling CLI budget needs an explicit USD quote")
    if amount_override_microusd is not None and (
        category != "KLING_CLI" or isinstance(amount_override_microusd, bool)
        or not isinstance(amount_override_microusd, int) or amount_override_microusd < 0
    ):
        raise ValueError("invalid Kling CLI USD quote")
    # Serializes image and video admission against the SAME account budget, including SQLite.
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(is_active=User.is_active)
        .execution_options(synchronize_session=False)
    )
    user = await session.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    if user is None or not user.is_active or user.role == UserRole.AUDITOR:
        raise HTTPException(403, detail={"code": "ACCOUNT_DISABLED", "message": "账号不能提交生成"})
    existing = await session.scalar(
        select(AccountUsage).where(
            AccountUsage.kind == kind,
            AccountUsage.operation_id == operation_id,
        )
    )
    if existing:
        if (existing.user_id != user_id or existing.units != units
                or (existing.pricing_category or existing.kind) != category):
            raise ValueError("usage operation conflicts with existing reservation")
        return existing
    prices, _, _ = await global_usage_prices(session)
    if category == "KLING_CLI":
        amount = amount_override_microusd
    elif category == "RECREATE_DEPTH_VIDEO":
        rates = [prices[key] for key in (
            "depthVideoPricePerSecondUsd", "recreateVideoPricePerSecondUsd",
        )]
        price = None if any(rate is None for rate in rates) else sum(rates)
        amount = None if price is None else price * units
    else:
        price = prices.get(CATEGORY_PRICES[category])
        amount = None if price is None else price * units
    day = business_date(now)
    if user.daily_budget_microusd is not None:
        active = (
            AccountUsage.user_id == user_id,
            AccountUsage.business_date == day,
            AccountUsage.status != "RELEASED",
        )
        unknown = await session.scalar(
            select(func.count())
            .select_from(AccountUsage)
            .where(
                *active,
                AccountUsage.amount_microusd.is_(None),
            )
        )
        if amount is None or unknown:
            raise HTTPException(
                409,
                detail={
                    "code": "USD_PRICE_REQUIRED",
                    "message": (
                        "美元预算无法核算：请配置预估单价；今日未定价记录需先核对或次日再提交。"
                    ),
                    "retryable": False,
                },
            )
        used = int(
            await session.scalar(
                select(
                    func.coalesce(
                        func.sum(AccountUsage.amount_microusd),
                        0,
                    )
                ).where(*active)
            )
            or 0
        )
        if used + amount > user.daily_budget_microusd:
            raise HTTPException(
                429,
                detail={
                    "code": "USD_DAILY_BUDGET_EXCEEDED",
                    "message": "今日美元预算不足，请联系管理员调整额度或次日再试。",
                    "retryable": False,
                    "details": {
                        "limitUsd": user.daily_budget_microusd / 1_000_000,
                        "usedUsd": used / 1_000_000,
                        "requestedUsd": amount / 1_000_000,
                    },
                },
            )
    record = AccountUsage(
        user_id=user_id,
        brand_id=user.brand_id,
        kind=kind,
        pricing_category=category,
        operation_id=operation_id,
        business_date=day,
        units=units,
        amount_microusd=amount,
        status="RESERVED",
    )
    session.add(record)
    await session.flush()
    return record


async def settle_usd(
    session: AsyncSession, *, kind: str, operation_id: uuid.UUID, status: str,
    actual_amount_microusd: int | None = None,
) -> None:
    if actual_amount_microusd is not None and (
        status != "ESTIMATED" or isinstance(actual_amount_microusd, bool)
        or not isinstance(actual_amount_microusd, int) or actual_amount_microusd < 0
    ):
        raise ValueError("invalid actual USD budget amount")
    values = {"status": status}
    if actual_amount_microusd is not None:
        values["amount_microusd"] = actual_amount_microusd
    await session.execute(
        update(AccountUsage)
        .where(
            AccountUsage.kind == kind,
            AccountUsage.operation_id == operation_id,
            AccountUsage.status.in_(("RESERVED", "UNCERTAIN")),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def brand_name(session: AsyncSession, brand_id: uuid.UUID | None) -> str | None:
    return (
        await session.scalar(select(Brand.name).where(Brand.id == brand_id)) if brand_id else None
    )
