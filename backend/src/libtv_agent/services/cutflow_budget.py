"""Prepaid CUT budget for opt-in fixed-video jobs.

The video admission transaction reserves video + editing budget together. Once
the automatic CUT task is created, the existing usage record is *transferred*
to the CUT task id; it must never be charged a second time.
"""
from __future__ import annotations

import uuid

from libtv_agent.domain.models import AccountUsage, MultiCutflowTask, ParentTask
from libtv_agent.services.account_access import reserve_usd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

BUDGET_KEY = "autoCutflowBudgetOperationId"


async def reserve_auto_cutflow(
    session: AsyncSession,
    *,
    parent: ParentTask,
    attempt_id: uuid.UUID,
    units: int,
) -> None:
    """Reserve editing at the same paid admission boundary as video generation."""
    if (parent.settings_json or {}).get("autoSubmitToCutflow") is not True:
        return
    prior = (parent.settings_json or {}).get(BUDGET_KEY)
    if prior:
        existing = await session.scalar(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
            AccountUsage.operation_id == uuid.UUID(prior),
            AccountUsage.user_id == parent.user_id,
        ))
        if existing is not None and existing.status == "RESERVED":
            if existing.units != units:
                raise ValueError("prepaid CUT source count changed")
            return
    operation_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"classic-auto-cutflow:{parent.id}:{attempt_id}"
    )
    await reserve_usd(
        session, user_id=parent.user_id, kind="CUTFLOW",
        operation_id=operation_id, units=units,
    )
    parent.settings_json = {**(parent.settings_json or {}), BUDGET_KEY: str(operation_id)}
    await session.flush()


async def bind_prepaid_cutflow(
    session: AsyncSession,
    *,
    parent: ParentTask,
    cutflow_task: MultiCutflowTask,
    units: int,
) -> None:
    """Transfer a held reservation to the actual CUT operation without charging twice."""
    prepaid = (parent.settings_json or {}).get(BUDGET_KEY)
    if prepaid:
        operation_id = uuid.UUID(prepaid)
        row = await session.scalar(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
            AccountUsage.operation_id == operation_id,
            AccountUsage.user_id == parent.user_id,
        ))
        if row is not None and row.status == "RESERVED":
            if not 0 < units <= row.units:
                raise ValueError("prepaid CUT source count increased")
            if row.amount_microusd is not None and row.amount_microusd % row.units:
                raise ValueError("prepaid CUT unit price cannot be reconciled")
            adjusted_amount = (
                None if row.amount_microusd is None
                else (row.amount_microusd // row.units) * units
            )
            moved = await session.execute(
                update(AccountUsage)
                .where(AccountUsage.id == row.id,
                       AccountUsage.status == "RESERVED",
                       AccountUsage.operation_id == operation_id)
                .values(operation_id=cutflow_task.id,
                        units=units, amount_microusd=adjusted_amount)
                .execution_options(synchronize_session=False)
            )
            if moved.rowcount != 1:
                raise RuntimeError("prepaid CUT reservation was claimed concurrently")
            return
    # Pre-cutover jobs and genuinely released reservations use the old admission.
    await reserve_usd(
        session, user_id=parent.user_id, kind="CUTFLOW",
        operation_id=cutflow_task.id, units=units,
    )


async def release_unused_auto_cutflow(
    session: AsyncSession, parent: ParentTask,
) -> bool:
    """Release only a parent-held reservation, never an already-created CUT job."""
    prepaid = (parent.settings_json or {}).get(BUDGET_KEY)
    if not prepaid:
        return False
    operation_id = uuid.UUID(prepaid)
    result = await session.execute(
        update(AccountUsage)
        .where(AccountUsage.kind == "CUTFLOW",
               AccountUsage.operation_id == operation_id,
               AccountUsage.user_id == parent.user_id,
               AccountUsage.status == "RESERVED")
        .values(status="RELEASED")
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
