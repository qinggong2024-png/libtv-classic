"""Read-only cross-account issue records for the management administrator.

Each row represents one generation attempt, except reservationReview, which
represents one unique PointsReservation. Ordinary /tasks access stays owner-only.
"""
from __future__ import annotations

from typing import Literal

from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase, ReservationStatus
from libtv_agent.domain.models import (
    Brand,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    PointsReservation,
    User,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

IssueCategory = Literal["cloudUnknown", "reservationReview", "blockedPrepared"]


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _value(value) -> str | None:
    return value.value if hasattr(value, "value") else value


async def list_operations_issues(
    session: AsyncSession,
    *,
    category: IssueCategory,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
) -> dict:
    """Fetch a bounded, sanitized diagnostic view without changing any task state."""
    statement = (
        select(
            ParentTask.id.label("task_id"),
            ParentTask.title.label("title"),
            ParentTask.phase.label("phase"),
            ParentTask.phase_reason_code.label("reason_code"),
            ParentTask.created_at.label("task_created_at"),
            User.username.label("username"),
            Brand.name.label("brand_name"),
            GenerationVariant.variant_type.label("variant_type"),
            GenerationAttempt.id.label("attempt_id"),
            GenerationAttempt.status.label("attempt_status"),
            GenerationAttempt.provider.label("provider"),
            GenerationAttempt.task_id.label("provider_task_id"),
            GenerationAttempt.provider_status.label("provider_status"),
            GenerationAttempt.last_error_code.label("last_error_code"),
            GenerationAttempt.error_message.label("error_message"),
            GenerationAttempt.charge_status.label("charge_status"),
            GenerationAttempt.submitted_at.label("submitted_at"),
            GenerationAttempt.updated_at.label("attempt_updated_at"),
            GenerationAttempt.last_reconciliation_at.label("reconciled_at"),
            GenerationAttempt.last_reconciliation_error.label("reconcile_error"),
            PointsReservation.id.label("reservation_id"),
            PointsReservation.status.label("reservation_status"),
            PointsReservation.charge_status.label("reservation_charge_status"),
            PointsReservation.estimated_points.label("estimated_points"),
            PointsReservation.actual_points.label("actual_points"),
            PointsReservation.created_at.label("reservation_created_at"),
        )
        .select_from(GenerationAttempt)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .join(User, User.id == ParentTask.user_id)
        .outerjoin(Brand, Brand.id == User.brand_id)
        .outerjoin(PointsReservation, PointsReservation.attempt_id == GenerationAttempt.id)
    )
    if category == "cloudUnknown":
        statement = statement.where(
            GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        )
        timestamp_column = GenerationAttempt.updated_at
    elif category == "reservationReview":
        statement = statement.where(PointsReservation.status == ReservationStatus.UNCERTAIN)
        timestamp_column = PointsReservation.created_at
    else:
        statement = statement.where(
            GenerationAttempt.status == AttemptStatus.PREPARED,
            ParentTask.phase.notin_(
                [ParentTaskPhase.QUEUED, ParentTaskPhase.GENERATING]
            ),
        )
        timestamp_column = GenerationAttempt.updated_at

    term = search.strip()
    if term:
        statement = statement.where(or_(
            ParentTask.title.ilike(f"%{term}%"),
            User.username.ilike(f"%{term}%"),
            Brand.name.ilike(f"%{term}%"),
        ))

    total = int(
        await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    rows = (
        await session.execute(
            statement.order_by(timestamp_column.desc(), GenerationAttempt.id.desc())
            .limit(limit).offset(offset)
        )
    ).mappings().all()
    items = [
        {
            "taskId": str(row["task_id"]),
            "title": row["title"],
            "username": row["username"],
            "brandName": row["brand_name"],
            "taskPhase": _value(row["phase"]),
            "phaseReasonCode": row["reason_code"],
            "taskCreatedAt": _iso(row["task_created_at"]),
            "variantType": _value(row["variant_type"]),
            "attemptId": str(row["attempt_id"]),
            "attemptStatus": _value(row["attempt_status"]),
            "provider": row["provider"],
            "providerTaskId": row["provider_task_id"],
            "providerStatus": row["provider_status"],
            "lastErrorCode": row["last_error_code"],
            "errorMessage": (row["error_message"] or "")[:300] or None,
            "chargeStatus": _value(row["charge_status"]),
            "submittedAt": _iso(row["submitted_at"]),
            "updatedAt": _iso(row["attempt_updated_at"]),
            "reconciledAt": _iso(row["reconciled_at"]),
            "reconcileError": (row["reconcile_error"] or "")[:300] or None,
            "reservationId": str(row["reservation_id"]) if row["reservation_id"] else None,
            "reservationStatus": _value(row["reservation_status"]),
            "reservationChargeStatus": _value(row["reservation_charge_status"]),
            "estimatedPoints": row["estimated_points"],
            "actualPoints": row["actual_points"],
            "reservationCreatedAt": _iso(row["reservation_created_at"]),
        }
        for row in rows
    ]
    return {
        "category": category,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }
