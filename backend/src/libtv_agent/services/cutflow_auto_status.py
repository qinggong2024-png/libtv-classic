"""Read-only, owner-scoped diagnosis of classic automatic CUT admission.

This endpoint never reserves budget, creates CUT work, or retries submissions.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import (
    AccountUsage,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
    User,
)
from libtv_agent.services import cutflow_observation
from libtv_agent.services.cutflow_auto_eligibility import eligible_auto_variants
from libtv_agent.services.cutflow_multi import MultiCutflowError, snapshot_sources
from libtv_agent.services.page_access import page_permissions
from libtv_agent.services.points import business_date
from libtv_agent.services.usage_pricing import CATEGORY_PRICES, global_usage_prices
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def auto_dispatch_status(
    session: AsyncSession,
    settings: Settings,
    actor_id: uuid.UUID,
    task_id: uuid.UUID,
) -> dict | None:
    task = await session.scalar(select(ParentTask).where(
        ParentTask.id == task_id, ParentTask.user_id == actor_id,
    ))
    if task is None:
        return None
    worker = cutflow_observation.worker_health(settings, datetime.now(UTC))
    base = {"taskId": str(task.id), "worker": worker, "state": "WAITING_DISPATCH",
            "reasonCode": None, "message": None, "batchId": None,
            "batchStatus": None, "requiredUsd": None, "remainingUsd": None}
    if (task.settings_json or {}).get("autoSubmitToCutflow") is not True:
        return {**base, "state": "MANUAL"}

    # This is a read of the user's already-persisted jobs, not another submission.
    jobs = (await session.scalars(select(MultiCutflowTask).where(
        MultiCutflowTask.user_id == actor_id,
    ).order_by(MultiCutflowTask.created_at.desc()))).all()
    match = next((job for job in jobs if job.idempotency_key == f"auto-unified-v2:{task.id}"
                  or any(str(source.get("taskId")) == str(task.id)
                         for source in (job.sources_json or []))), None)
    if match is not None:
        return {**base, "state": "RECORDED", "batchId": str(match.id),
                "batchStatus": match.status, "reasonCode": match.error_code}

    variants = (await session.scalars(select(GenerationVariant).where(
        GenerationVariant.parent_task_id == task.id,
    ).order_by(GenerationVariant.variant_index, GenerationVariant.id))).all()
    selected = eligible_auto_variants(variants, task.phase)
    if selected is None:
        return {**base, "state": "WAITING_ORIGINALS"}
    if not selected:
        return {**base, "state": "BLOCKED", "reasonCode": "NO_USABLE_ORIGINALS",
                "message": "本次生成已结束，但没有通过校验的可用原片。请处理失败片段。"}

    user = await session.get(User, actor_id)
    if (user is None or not user.is_active or user.role == UserRole.AUDITOR
            or "make" not in page_permissions(user)
            or user.daily_budget_microusd == 0):
        return {**base, "state": "BLOCKED", "reasonCode": "ACCOUNT_NOT_ELIGIBLE",
                "message": "当前账号无自动送剪权限或预算额度，请联系管理员核对。"}

    try:
        await snapshot_sources(session, actor_id, [v.id for v in selected],
                               allow_single_source=len(selected) == 1)
    except MultiCutflowError as exc:
        return {**base, "state": "BLOCKED", "reasonCode": exc.code, "message": str(exc)}

    # Video admission can already hold the CUT fee. That reservation counts in
    # today's usage, so a second available-budget check would double-count it.
    prepaid_key = (task.settings_json or {}).get("autoCutflowBudgetOperationId")
    prepaid_reserved = False
    if prepaid_key:
        try:
            prepaid_id = uuid.UUID(str(prepaid_key))
        except (TypeError, ValueError, AttributeError):
            prepaid_id = None
        if prepaid_id is not None:
            prepaid = await session.scalar(select(AccountUsage).where(
                AccountUsage.user_id == actor_id,
                AccountUsage.kind == "CUTFLOW",
                AccountUsage.operation_id == prepaid_id,
                AccountUsage.status == "RESERVED",
            ))
            prepaid_reserved = prepaid is not None and prepaid.units >= len(selected)

    # Older tasks, released reservations and malformed keys still require
    # normal budget eligibility checks; no budget bypass is permitted.
    if user.daily_budget_microusd is not None and not prepaid_reserved:
        prices, _conflicts, _version = await global_usage_prices(session)
        rate = prices.get(CATEGORY_PRICES["CUTFLOW"])
        active = (AccountUsage.user_id == actor_id,
                  AccountUsage.business_date == business_date(),
                  AccountUsage.status != "RELEASED")
        total = await session.execute(select(
            func.coalesce(func.sum(AccountUsage.amount_microusd), 0),
            func.sum(case((AccountUsage.amount_microusd.is_(None), 1), else_=0)),
        ).where(*active))
        used, unknown = total.one()
        if rate is None or unknown:
            return {**base, "state": "BLOCKED", "reasonCode": "USD_PRICE_REQUIRED",
                    "message": "当前剪辑单价或历史费用待核实，自动送剪暂不能创建任务。"}
        required = int(rate) * len(selected)
        remaining = int(user.daily_budget_microusd) - int(used or 0)
        if required > remaining:
            return {**base, "state": "BLOCKED",
                    "reasonCode": "USD_DAILY_BUDGET_EXCEEDED",
                    "message": "今日美元预算剩余额度不足，未创建 CUT 剪辑任务。",
                    "remainingUsd": round(max(0, remaining) / 1_000_000, 2),
                    "requiredUsd": round(required / 1_000_000, 2)}
    if worker["state"] != "online":
        return {**base, "state": "WORKER_UNAVAILABLE",
                "message": "自动送剪 Worker 心跳未确认，请检查服务运行状态。"}
    return base
