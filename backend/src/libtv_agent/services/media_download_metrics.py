"""Audit user-triggered media downloads without affecting paid production.

An authorized HTTP download request is recorded after file lookup. Reports count
unique files per account by first download date; browser disk save is unverifiable.
"""

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta

from libtv_agent.domain.models import AuditEvent
from libtv_agent.services.points import SHANGHAI
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ORIGINAL_DOWNLOAD = "DOWNLOAD_ORIGINAL_VIDEO"
CUTFLOW_DOWNLOAD = "DOWNLOAD_CUTFLOW_VIDEO"


async def record_download(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: str,
    media_id: str,
) -> None:
    """Log only explicitly requested downloads, after ownership and file validation."""
    if action not in {ORIGINAL_DOWNLOAD, CUTFLOW_DOWNLOAD}:
        raise ValueError("unsupported download type")
    session.add(AuditEvent(
        id=uuid.uuid4(),
        actor_id=user_id,
        action=action,
        entity_type="video_download",
        entity_id=media_id[:160],
        correlation_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
    ))
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Unable to record %s for user %s", action, user_id)
        # Do not withhold an existing downloadable video due to analytics failure.


async def aggregate_downloads(
    session: AsyncSession,
    scope,
    start: date,
    end: date,
) -> dict[uuid.UUID, dict[str, int]]:
    start_utc = datetime.combine(start, time.min, SHANGHAI).astimezone(UTC)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, SHANGHAI).astimezone(UTC)
    # De-duplicate across the entire history, not just inside the selected date
    # window. A later repeat download must not inflate another day's numbers.
    first_downloads = (
        select(
            AuditEvent.actor_id.label("user_id"),
            AuditEvent.action.label("action"),
            AuditEvent.entity_id.label("media_id"),
            func.min(AuditEvent.created_at).label("first_at"),
        )
        .where(
            AuditEvent.actor_id.in_(scope),
            AuditEvent.action.in_((ORIGINAL_DOWNLOAD, CUTFLOW_DOWNLOAD)),
            AuditEvent.entity_type == "video_download",
        )
        .group_by(AuditEvent.actor_id, AuditEvent.action, AuditEvent.entity_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            first_downloads.c.user_id,
            func.count(case((first_downloads.c.action == ORIGINAL_DOWNLOAD, 1))).label("originalDownloads"),
            func.count(case((first_downloads.c.action == CUTFLOW_DOWNLOAD, 1))).label("cutflowDownloads"),
        )
        .where(
            first_downloads.c.first_at >= start_utc,
            first_downloads.c.first_at < end_utc,
        )
        .group_by(first_downloads.c.user_id)
    )
    return {
        row.user_id: {
            "originalDownloads": int(row.originalDownloads),
            "cutflowDownloads": int(row.cutflowDownloads),
        }
        for row in rows
    }
