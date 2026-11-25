"""Shared provider evidence classification and conservative repeat-rejection preflight.

Risk-control history is scoped to the same user, exact product-reference image
set and recent terminal provider responses. It never changes prompts or images.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from libtv_agent.domain.enums import AttemptStatus
from libtv_agent.domain.models import Asset, GenerationAttempt, GenerationVariant, ParentTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

KLING_RISK_CONTROL_ERROR = "KLING_REMOTE_RISK_CONTROL_REJECTED"
_RISK_CONTROL_PHRASE = "failure to pass the risk control system"


def kling_risk_control_rejected(message: str | None) -> bool:
    return isinstance(message, str) and _RISK_CONTROL_PHRASE in message.casefold()


@dataclass(frozen=True)
class RepeatedRiskControl:
    failed_remote_task_count: int
    prior_task_count: int


async def repeated_product_risk_control(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    product_hashes: tuple[str, ...],
    exclude_task_id: uuid.UUID | None = None,
    lookback_days: int = 14,
) -> RepeatedRiskControl | None:
    """Protect only a repeat product with >=2 confirmed rejections and NO success.

    Fail open on insufficient history. No text heuristics, approximate image
    matches, cross-user matches, or indefinite bans.
    """
    if not product_hashes or len(set(product_hashes)) != len(product_hashes):
        return None
    task_query = (
        select(Asset.parent_task_id)
        .join(ParentTask, ParentTask.id == Asset.parent_task_id)
        .where(
            ParentTask.user_id == user_id,
            ParentTask.workflow_kind == "FIXED_TEMPLATE",
            ParentTask.created_at >= datetime.now(UTC) - timedelta(days=lookback_days),
            Asset.role == "PRODUCT",
            Asset.sha256.in_(product_hashes),
        )
        .distinct()
        .order_by(Asset.parent_task_id)
        .limit(200)
    )
    if exclude_task_id is not None:
        task_query = task_query.where(Asset.parent_task_id != exclude_task_id)
    task_ids = tuple((await session.scalars(task_query)).all())
    if not task_ids:
        return None
    assets = (await session.execute(
        select(Asset.parent_task_id, Asset.sha256)
        .where(Asset.parent_task_id.in_(task_ids), Asset.role == "PRODUCT")
        .order_by(Asset.order_no)
    )).all()
    products_by_task: dict[uuid.UUID, list[str]] = {}
    for task_id, sha in assets:
        products_by_task.setdefault(task_id, []).append(sha)
    matched_task_ids = tuple(
        task_id for task_id, hashes in products_by_task.items()
        if tuple(hashes) == product_hashes
    )
    if not matched_task_ids:
        return None
    attempts = (await session.scalars(
        select(GenerationAttempt)
        .join(GenerationVariant, GenerationAttempt.variant_id == GenerationVariant.id)
        .where(
            GenerationVariant.parent_task_id.in_(matched_task_ids),
            GenerationAttempt.provider == "kling_official_api",
        )
    )).all()
    rejected_remote_ids: set[str] = set()
    rejected_task_ids: set[uuid.UUID] = set()
    # Map variants once rather than assuming the error code survived a callback.
    variant_task_ids = dict((await session.execute(
        select(GenerationVariant.id, GenerationVariant.parent_task_id)
        .where(GenerationVariant.parent_task_id.in_(matched_task_ids))
    )).all())
    for attempt in attempts:
        if attempt.status == AttemptStatus.SUCCEEDED:
            return None
        if (attempt.status == AttemptStatus.FAILED_TERMINAL
                and attempt.task_id
                and (attempt.last_error_code == KLING_RISK_CONTROL_ERROR
                     or kling_risk_control_rejected(attempt.error_message))):
            rejected_remote_ids.add(attempt.task_id)
            rejected_task_ids.add(variant_task_ids[attempt.variant_id])
    if len(rejected_remote_ids) < 2:
        return None
    return RepeatedRiskControl(
        failed_remote_task_count=len(rejected_remote_ids),
        prior_task_count=len(rejected_task_ids),
    )
