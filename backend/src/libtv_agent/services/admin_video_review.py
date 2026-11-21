"""Platform administrator's read-only generation-video inspection queries."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from libtv_agent.domain.enums import DownloadStatus
from libtv_agent.domain.models import (
    Brand,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    User,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


def _text(value):
    return value.value if hasattr(value, "value") else value


async def list_admin_videos(
    session: AsyncSession,
    *,
    days: int = 7,
    limit: int = 24,
    offset: int = 0,
    search: str = "",
    brand_id: uuid.UUID | None = None,
) -> dict:
    """Return actual stored, recent generation clips across all accounts."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    base = (
        select(
            DownloadArtifact.id.label("artifact_id"),
            DownloadArtifact.created_at.label("created_at"),
            DownloadArtifact.duration_seconds.label("duration_seconds"),
            DownloadArtifact.width.label("width"),
            DownloadArtifact.height.label("height"),
            GenerationAttempt.id.label("attempt_id"),
            GenerationAttempt.provider.label("provider"),
            GenerationVariant.variant_type.label("variant"),
            GenerationVariant.validation_status.label("validation_status"),
            GenerationVariant.final_approval_status.label("final_approval_status"),
            ParentTask.id.label("task_id"),
            ParentTask.title.label("title"),
            User.username.label("username"),
            Brand.id.label("brand_id"),
            Brand.name.label("brand_name"),
        )
        .select_from(DownloadArtifact)
        .join(GenerationAttempt, GenerationAttempt.id == DownloadArtifact.attempt_id)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .join(User, User.id == ParentTask.user_id)
        .outerjoin(Brand, Brand.id == User.brand_id)
        .where(
            DownloadArtifact.status == DownloadStatus.READY,
            DownloadArtifact.object_key.is_not(None),
            DownloadArtifact.deleted_at.is_(None),
            DownloadArtifact.created_at >= cutoff,
        )
    )
    if brand_id is not None:
        base = base.where(Brand.id == brand_id)
    if search.strip():
        term = search.strip()
        base = base.where(or_(
            ParentTask.title.ilike(f"%{term}%"),
            User.username.ilike(f"%{term}%"),
            Brand.name.ilike(f"%{term}%"),
        ))
    count = int(await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0)
    rows = (await session.execute(
        base.order_by(
            DownloadArtifact.created_at.desc(),
            DownloadArtifact.id.desc(),
        ).limit(limit).offset(offset)
    )).mappings().all()
    return {
        "total": count,
        "limit": limit,
        "offset": offset,
        "days": days,
        "items": [{
            "artifactId": str(r["artifact_id"]),
            "taskId": str(r["task_id"]),
            "attemptId": str(r["attempt_id"]),
            "title": r["title"],
            "username": r["username"],
            "brandId": str(r["brand_id"]) if r["brand_id"] else None,
            "brandName": r["brand_name"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
            "durationSeconds": r["duration_seconds"],
            "width": r["width"],
            "height": r["height"],
            "variant": _text(r["variant"]),
            "provider": r["provider"],
            "validationStatus": _text(r["validation_status"]),
            "finalApprovalStatus": _text(r["final_approval_status"]),
        } for r in rows],
    }


async def get_admin_video_artifact(
    session: AsyncSession, artifact_id: uuid.UUID
) -> DownloadArtifact:
    """Validate an existing saved clip ID; never accept a client-provided path."""
    artifact = await session.scalar(select(DownloadArtifact).where(
        DownloadArtifact.id == artifact_id,
        DownloadArtifact.status == DownloadStatus.READY,
        DownloadArtifact.object_key.is_not(None),
        DownloadArtifact.deleted_at.is_(None),
    ))
    if artifact is None:
        raise HTTPException(404, "video is not available for review")
    return artifact
