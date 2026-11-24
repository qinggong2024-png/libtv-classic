"""Read-only employee activity projection. Creation is not successful output.

Keep the legacy audit endpoint intact. One durable task/batch is one creation;
provider attempts, polling and budget reservations never become new tasks.
"""
from datetime import UTC, datetime, time, timedelta

from libtv_agent.domain.models import (
    AuditEvent, CutflowHandoff, ImageGenerationBatch, MultiCutflowTask, ParentTask, User,
)
from libtv_agent.services.media_download_metrics import CUTFLOW_DOWNLOAD, ORIGINAL_DOWNLOAD
from libtv_agent.services.points import SHANGHAI
from sqlalchemy import Integer, String, case, cast, func, literal, select, union_all

SYSTEM_ACTIONS = (
    "LOGIN", "LOGOUT", "CREATE_USER", "CREATE_BRAND", "UPDATE_ACCOUNT_POLICY",
    "RESET_USER_PASSWORD", "DEACTIVATE_USER", "CHANGE_PASSWORD",
    "BACH_CONFIG_UPDATED", "WAVESPEED_CONFIG_UPDATED", "KLING_CONFIG_UPDATED",
    "UPDATE_PROVIDER_CREDENTIALS", "REFRESH_MODEL_SCHEMA",
)
VIDEO_ACTIONS = ("EDIT_TASK_PROMPTS", "REGENERATE_TASK_PROMPTS", "CONFIRM_TASK")
REVIEW_ACTIONS = ("FINAL_VIDEO_APPROVAL", "IMAGE_QUALITY_DECISION")
DOWNLOAD_ACTIONS = (ORIGINAL_DOWNLOAD, CUTFLOW_DOWNLOAD)


async def production_activity(session, scope, start, end, category, offset, limit):
    start_at = datetime.combine(start, time.min, SHANGHAI).astimezone(UTC)
    end_at = datetime.combine(end + timedelta(days=1), time.min, SHANGHAI).astimezone(UTC)

    def within(owner, at):
        return (owner.in_(scope), at >= start_at, at < end_at)

    def source(model, action, group, entity_type, quantity, detail=None):
        return select(
            (literal(entity_type + ":") + cast(model.id, String)).label("id"),
            model.user_id.label("actor_id"), literal(action).label("action"),
            literal(group).label("category"), model.created_at.label("created_at"),
            literal(entity_type).label("entity_type"), cast(model.id, String).label("entity_id"),
            cast(quantity, Integer).label("quantity"),
            cast(detail if detail is not None else literal(None), String).label("detail_title"),
            cast(literal(None), String).label("outcome"),
        ).where(*within(model.user_id, model.created_at))

    # The immutable submission count (not seconds, variants rebuilt on retry,
    # or successful artifacts). Older tasks without this field have unknown
    # requested video count, and are shown as a single task without a count.
    video_count = ParentTask.settings_json["resultCount"].as_integer()
    video = source(ParentTask, "CREATE_VIDEO_TASK", "VIDEO", "parent_task",
                   case((video_count > 0, video_count), else_=None), ParentTask.title)
    images = source(ImageGenerationBatch, "CREATE_IMAGE_TASK", "IMAGE",
                    "image_generation_batch", ImageGenerationBatch.expected_generation_count,
                    ImageGenerationBatch.name)
    # A local handoff is not proof that CUT accepted it or completed an edit.
    multi_cut = source(MultiCutflowTask, "CREATE_CUT_TASK", "CUTFLOW", "multi_cutflow_task",
                       func.json_array_length(MultiCutflowTask.sources_json))
    single_cut = source(CutflowHandoff, "CREATE_CUT_TASK", "CUTFLOW", "cutflow_handoff", literal(1))

    audit = select(
        (literal("audit:") + cast(AuditEvent.id, String)).label("id"),
        AuditEvent.actor_id.label("actor_id"), AuditEvent.action.label("action"),
        case((AuditEvent.action.in_(REVIEW_ACTIONS), "REVIEW"),
             (AuditEvent.action.in_(VIDEO_ACTIONS), "VIDEO"), else_="SYSTEM").label("category"),
        AuditEvent.created_at.label("created_at"), AuditEvent.entity_type.label("entity_type"),
        AuditEvent.entity_id.label("entity_id"),
        cast(case((AuditEvent.action.in_(REVIEW_ACTIONS), 1), else_=None), Integer).label("quantity"),
        cast(literal(None), String).label("detail_title"),
        case((AuditEvent.action.in_(REVIEW_ACTIONS), AuditEvent.to_state), else_=None).label("outcome"),
    ).where(*within(AuditEvent.actor_id, AuditEvent.created_at),
            AuditEvent.action.in_(SYSTEM_ACTIONS + VIDEO_ACTIONS + REVIEW_ACTIONS))

    # Match usage statistics: the first explicit download request per user/file
    # across all history. Repeated clicks and previews do not inflate counts.
    first = select(
        AuditEvent.actor_id.label("actor_id"), AuditEvent.action.label("action"),
        AuditEvent.entity_id.label("entity_id"), func.min(AuditEvent.created_at).label("created_at"),
    ).where(AuditEvent.actor_id.in_(scope), AuditEvent.action.in_(DOWNLOAD_ACTIONS),
            AuditEvent.entity_type == "video_download").group_by(
        AuditEvent.actor_id, AuditEvent.action, AuditEvent.entity_id
    ).subquery()
    downloads = select(
        (literal("download:") + cast(first.c.actor_id, String) + literal(":") +
         first.c.action + literal(":") + first.c.entity_id).label("id"),
        first.c.actor_id, first.c.action, literal("DOWNLOAD").label("category"),
        first.c.created_at, literal("video_download").label("entity_type"), first.c.entity_id,
        literal(1).label("quantity"), cast(literal(None), String).label("detail_title"),
        cast(literal(None), String).label("outcome"),
    ).where(first.c.created_at >= start_at, first.c.created_at < end_at)

    feed = union_all(video, images, multi_cut, single_cut, audit, downloads).subquery()
    query = select(feed, User.username).join(User, User.id == feed.c.actor_id)
    count = select(func.count()).select_from(feed)
    if category:
        query = query.where(feed.c.category == category)
        count = count.where(feed.c.category == category)
    total = int(await session.scalar(count) or 0)
    rows = (await session.execute(
        query.order_by(feed.c.created_at.desc(), feed.c.id.desc()).offset(offset).limit(limit)
    )).mappings().all()
    return {
        "view": "production", "startDate": start.isoformat(), "endDate": end.isoformat(), "total": total,
        "items": [{
            "id": row["id"], "username": row["username"], "action": row["action"],
            "category": row["category"], "createdAt": row["created_at"].isoformat(),
            "entityType": row["entity_type"], "entityId": row["entity_id"],
            "quantity": row["quantity"], "detailTitle": row["detail_title"],
            "outcome": row["outcome"],
        } for row in rows],
    }
