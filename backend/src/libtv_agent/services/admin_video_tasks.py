"""Read-only admin task gallery that keeps CUT deliverables separate from Kling clips."""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libtv_agent.domain.models import Brand, CutflowHandoff, MultiCutflowTask, ParentTask, User
from libtv_agent.services.admin_video_alerts import SEVERITY_RANK
from libtv_agent.services.admin_video_review import list_admin_videos


def _timestamp(value):
    if not value:
        return 0.0
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _output(row, kind):
    """Only imported media with an object-store key counts as a playable CUT result."""
    items = row.output_json if kind == "multi" else row.outputs_json
    valid = [o for o in (items or []) if isinstance(o, dict)
             and o.get("id") and isinstance(o.get("object_key"), str)
             and o["object_key"]]
    if not valid:
        return None
    latest = max(valid, key=lambda o: _timestamp(o.get("created_at")))
    return {
        "kind": kind, "batchId": str(row.id), "outputId": str(latest["id"]),
        "durationSeconds": latest.get("duration_seconds"),
        "createdAt": latest.get("created_at"), "status": row.status,
    }


async def list_admin_video_tasks(
    session: AsyncSession, *, days: int, limit: int, offset: int,
    search: str, brand_id: uuid.UUID | None, health_filter: str, sort: str,
) -> dict:
    # The existing alert classifier already evaluates all clips before pagination.
    # Group *before* slicing so A/B and retry outputs cannot split across pages.
    result = await list_admin_videos(
        session, days=days, limit=1_000_000, offset=0, search=search,
        brand_id=brand_id, health_filter="all", sort="newest",
    )
    groups: dict[str, list[dict]] = defaultdict(list)
    for clip in result["items"]:
        groups[clip["taskId"]].append(clip)
    # A task is inspectable even when no download artifact exists yet.
    # Keep the existing recent-artifact scope and add tasks created in the window.
    cutoff = datetime.now(UTC) - timedelta(days=days)
    parent_query = (
        select(ParentTask.id, ParentTask.user_id, ParentTask.title,
               ParentTask.created_at, ParentTask.phase, ParentTask.phase_reason_code, ParentTask.workflow_kind,
               ParentTask.analysis_started_at, ParentTask.analysis_stage,
               User.username, Brand.name.label("brand_name"))
        .join(User, User.id == ParentTask.user_id)
        .outerjoin(Brand, Brand.id == User.brand_id)
        .where(or_(ParentTask.created_at >= cutoff, ParentTask.id.in_(
            [uuid.UUID(task_id) for task_id in groups]
        )))
    )
    if brand_id is not None:
        parent_query = parent_query.where(Brand.id == brand_id)
    if search.strip():
        term = f"%{search.strip()}%"
        parent_query = parent_query.where(or_(
            ParentTask.title.ilike(term), User.username.ilike(term), Brand.name.ilike(term),
        ))
    parents = {str(row.id): row for row in (await session.execute(parent_query)).all()}
    for task_id in parents:
        groups.setdefault(task_id, [])
    if not groups:
        return {"total": 0, "limit": limit, "offset": offset, "days": days,
                "healthSummary": {"critical": 0, "warning": 0, "normal": 0},
                "items": []}

    ids = {uuid.UUID(task_id) for task_id in groups}
    artifact_tasks = {c["artifactId"]: c["taskId"] for c in result["items"]}
    # Restrict multi-source lookups to actual owners of the matched tasks.
    owners = {row.user_id for row in parents.values()}
    singles = (await session.scalars(select(CutflowHandoff).where(
        CutflowHandoff.parent_task_id.in_(ids),
    ))).all()
    cutoff = datetime.now(UTC) - timedelta(days=days)
    multi_rows = (await session.scalars(select(MultiCutflowTask).where(
        MultiCutflowTask.user_id.in_(owners),
        MultiCutflowTask.created_at >= cutoff,
    ))).all()

    cut_by_task: dict[str, list[tuple[float, dict | None, str, str, list[str]]]] = defaultdict(list)
    for row in singles:
        task_id = str(row.parent_task_id)
        if task_id in groups:
            cut_by_task[task_id].append((
                _timestamp(row.updated_at or row.created_at), _output(row, "single"),
                row.status, "single", [str(row.artifact_id)],
            ))
    for row in multi_rows:
        source_ids = [str(s.get("artifactId") or "") for s in (row.sources_json or [])
                      if isinstance(s, dict)]
        matched = {str(s.get("taskId") or artifact_tasks.get(str(s.get("artifactId") or ""), ""))
                   for s in (row.sources_json or []) if isinstance(s, dict)}
        for task_id in matched & groups.keys():
            cut_by_task[task_id].append((
                _timestamp(row.updated_at or row.created_at), _output(row, "multi"),
                row.status, "multi", source_ids,
            ))

    tasks = []
    for task_id, all_clips in groups.items():
        parent = parents[task_id]
        newest = max(all_clips, key=lambda v: _timestamp(v["createdAt"]), default=None)
        candidates = sorted(cut_by_task[task_id], reverse=True, key=lambda item: item[0])
        # Prefer the newest actual deliverable; a retry in progress does not
        # make a previously imported CUT video disappear.
        deliverable = next((c for c in candidates if c[1] is not None), None)
        current_cut = candidates[0] if candidates else None
        source_ids = set(deliverable[4]) if deliverable else set()
        by_variant = {}
        for clip in all_clips:
            identity = (f"index:{clip['variantIndex']}" if clip.get("variantIndex") is not None
                        else f"id:{clip.get('variantId') or clip['attemptId']}")
            prev = by_variant.get(identity)
            preferred = (clip["artifactId"] in source_ids, _timestamp(clip["createdAt"]))
            if prev is None or preferred > (
                prev["artifactId"] in source_ids, _timestamp(prev["createdAt"])
            ):
                by_variant[identity] = clip
        clips = sorted(by_variant.values(), key=lambda c: (
            c.get("variantIndex") if c.get("variantIndex") is not None else
            (1 if c["variant"] == "STABLE" else 2 if c["variant"] == "DYNAMIC" else 99),
            c["createdAt"] or "",
        ))
        severity = min(
            (c.get("health", {}).get("severity", "normal") for c in all_clips),
            key=lambda s: SEVERITY_RANK.get(s, 99), default="normal",
        )
        if parent.phase == "ACTION_REQUIRED":
            severity = "critical"
        elif parent.phase == "PARTIALLY_DELIVERABLE" and severity == "normal":
            severity = "warning"
        tasks.append({
            "taskId": task_id, "title": parent.title,
            "username": parent.username, "brandName": parent.brand_name,
            "createdAt": parent.created_at.isoformat(),
            "latestRawAt": newest["createdAt"] if newest else None,
            "phase": parent.phase.value,
            "workflowKind": parent.workflow_kind,
            "phaseReasonCode": parent.phase_reason_code,
            "analysisStage": parent.analysis_stage,
            "analysisStartedAt": parent.analysis_started_at.isoformat() if parent.analysis_started_at else None,
            "clips": clips,
            "rawClipCount": len(all_clips), "severity": severity,
            "cut": deliverable[1] if deliverable else None,
            "cutStatus": current_cut[2] if current_cut else None,
        })
    summary = Counter(t["severity"] for t in tasks)
    if health_filter != "all":
        tasks = [t for t in tasks if t["severity"] == health_filter]
    tasks.sort(key=lambda t: (
        SEVERITY_RANK.get(t["severity"], 99) if sort == "priority" else 0,
        -_timestamp(t["createdAt"]), t["taskId"],
    ))
    return {
        "total": len(tasks), "limit": limit, "offset": offset, "days": days,
        "healthSummary": {s: summary[s] for s in ("critical", "warning", "normal")},
        "items": tasks[offset:offset + limit],
    }


async def admin_cut_media_output(
    session: AsyncSession, task_id: uuid.UUID, kind: str,
    batch_id: uuid.UUID, output_id: str,
) -> dict:
    """Resolve media only through a verified stored CUT output and task link."""
    if not output_id or len(output_id) > 200:
        raise HTTPException(404, "CUT output unavailable")
    if kind == "single":
        row = await session.scalar(select(CutflowHandoff).where(
            CutflowHandoff.id == batch_id,
            CutflowHandoff.parent_task_id == task_id,
        ))
        outputs = row.outputs_json if row else []
    elif kind == "multi":
        row = await session.get(MultiCutflowTask, batch_id)
        linked = row is not None and any(
            isinstance(s, dict) and s.get("taskId") == str(task_id)
            for s in (row.sources_json or [])
        )
        outputs = row.output_json if linked else []
    else:
        raise HTTPException(404, "CUT output unavailable")
    output = next((o for o in outputs or [] if isinstance(o, dict)
                   and str(o.get("id")) == output_id), None)
    if not output or not isinstance(output.get("object_key"), str) or not output["object_key"]:
        raise HTTPException(404, "CUT output unavailable")
    return output
