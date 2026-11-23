"""Platform administrator's read-only generation-video inspection queries."""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from libtv_agent.domain.enums import DownloadStatus
from libtv_agent.domain.models import (
    Brand,
    CutflowHandoff,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
    User,
    ValidationResult,
)
from libtv_agent.services.admin_video_alerts import SEVERITY_RANK, classify_saved_videos
from sqlalchemy import or_, select
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
    health_filter: str = "all",
    sort: str = "priority",
) -> dict:
    """Classify all matching saved clips before pagination and alert-first ordering.

    Cut and validation evidence is bulk-loaded. No clip is scored by a model and
    the page is never an instruction to retry a paid provider operation.
    """
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
            GenerationAttempt.submitted_at.label("submitted_at"),
            GenerationAttempt.completed_at.label("completed_at"),
            GenerationVariant.confirmed_at.label("confirmed_at"),
            GenerationVariant.generation_config_json.label("generation_config"),
            GenerationVariant.variant_type.label("variant"),
            GenerationVariant.id.label("variant_id"),
            GenerationVariant.variant_index.label("variant_index"),
            GenerationVariant.validation_status.label("validation_status"),
            GenerationVariant.final_approval_status.label("final_approval_status"),
            ParentTask.id.label("task_id"),
            ParentTask.title.label("title"),
            User.id.label("user_id"),
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

    rows = [dict(row) for row in (await session.execute(base)).mappings().all()]
    if not rows:
        return {
            "total": 0, "limit": limit, "offset": offset, "days": days,
            "healthFilter": health_filter, "sort": sort,
            "healthSummary": {"critical": 0, "warning": 0, "normal": 0},
            "items": [],
        }

    # Compare each clip to the same rolling 30-day provider/director cohort,
    # independent of what the admin searched or which page is being viewed.
    baseline = [dict(row) for row in (await session.execute(
        select(
            DownloadArtifact.created_at.label("created_at"),
            GenerationAttempt.submitted_at.label("submitted_at"),
            GenerationAttempt.completed_at.label("completed_at"),
            GenerationAttempt.provider.label("provider"),
            GenerationVariant.confirmed_at.label("confirmed_at"),
            GenerationVariant.generation_config_json.label("generation_config"),
        )
        .select_from(DownloadArtifact)
        .join(GenerationAttempt, GenerationAttempt.id == DownloadArtifact.attempt_id)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .where(
            DownloadArtifact.status == DownloadStatus.READY,
            DownloadArtifact.created_at >= datetime.now(UTC) - timedelta(days=30),
        )
    )).mappings().all()]

    validation_latest: dict[str, dict] = {}
    for ids in _chunks([row["attempt_id"] for row in rows]):
        validations = (await session.execute(
            select(
                ValidationResult.attempt_id,
                ValidationResult.status,
                ValidationResult.created_at,
            )
            .where(ValidationResult.attempt_id.in_(ids))
            .order_by(ValidationResult.created_at.desc(), ValidationResult.id.desc())
        )).mappings().all()
        for item in validations:
            validation_latest.setdefault(str(item["attempt_id"]), dict(item))

    cut_latest: dict[str, dict] = {}
    owners = {str(row["artifact_id"]): row["user_id"] for row in rows}
    attempts = {str(row["attempt_id"]): str(row["artifact_id"]) for row in rows}
    for ids in _chunks([row["artifact_id"] for row in rows]):
        single_rows = (await session.execute(
            select(
                CutflowHandoff.artifact_id, CutflowHandoff.status,
                CutflowHandoff.created_at, CutflowHandoff.updated_at,
            ).where(CutflowHandoff.artifact_id.in_(ids))
        )).mappings().all()
        for item in single_rows:
            _add_cut_state(cut_latest, str(item["artifact_id"]), {
                "status": item["status"], "updated_at": item["updated_at"],
                "created_at": item["created_at"], "source": "cutflow_handoff.status",
            })

    user_ids = sorted({row["user_id"] for row in rows}, key=str)
    for ids in _chunks(user_ids):
        multi_rows = (await session.execute(
            select(
                MultiCutflowTask.user_id, MultiCutflowTask.sources_json,
                MultiCutflowTask.status, MultiCutflowTask.updated_at,
                MultiCutflowTask.created_at,
            )
            .where(
                MultiCutflowTask.user_id.in_(ids),
                MultiCutflowTask.created_at >= cutoff,
            )
        )).mappings().all()
        for item in multi_rows:
            sources = item["sources_json"]
            if not isinstance(sources, list):
                continue
            for source in sources:
                if not isinstance(source, dict):
                    continue
                artifact_id = str(source.get("artifactId") or "")
                if artifact_id not in owners:
                    artifact_id = attempts.get(str(source.get("attemptId") or ""), "")
                if artifact_id and owners.get(artifact_id) == item["user_id"]:
                    _add_cut_state(cut_latest, artifact_id, {
                        "status": item["status"], "updated_at": item["updated_at"],
                        "created_at": item["created_at"],
                        "source": "multi_cutflow_task.status",
                    })

    classified = classify_saved_videos(rows, validation_latest, cut_latest, baseline)
    severity_counts = Counter(row["health"]["severity"] for row in classified)
    summary = {
        "critical": severity_counts["critical"],
        "warning": severity_counts["warning"],
        "normal": severity_counts["normal"],
    }
    if health_filter != "all":
        classified = [
            row for row in classified if row["health"]["severity"] == health_filter
        ]
    classified.sort(
        key=lambda row: (
            (SEVERITY_RANK[row["health"]["severity"]] if sort == "priority" else 0),
            -_utc_epoch(row["created_at"]),
            str(row["artifact_id"]),
        )
    )
    total = len(classified)
    page_rows = classified[offset:offset + limit]
    return {
        "total": total, "limit": limit, "offset": offset,
        "days": days, "healthFilter": health_filter, "sort": sort,
        "healthSummary": summary,
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
            "variantId": str(r["variant_id"]),
            "variantIndex": r["variant_index"],
            "provider": r["provider"],
            "validationStatus": _text(r["validation_status"]),
            "finalApprovalStatus": _text(r["final_approval_status"]),
            "health": r["health"],
        } for r in page_rows],
    }


def _chunks(items: list, size: int = 400):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _utc_epoch(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _add_cut_state(latest: dict[str, dict], artifact_id: str, value: dict):
    existing = latest.get(artifact_id)
    if existing is None or _utc_epoch(
        value.get("updated_at") or value.get("created_at")
    ) > _utc_epoch(existing.get("updated_at") or existing.get("created_at")):
        latest[artifact_id] = value


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
