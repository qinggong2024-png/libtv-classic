"""Opt-in durable manual analysis; separate from automatic video planning.

The existing run ID fences stale writers. Only a claim that has not entered
analysis can be replayed automatically. Once analysis starts, existing heartbeat
and terminal recovery semantics apply; never replay an ambiguous provider call.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import ParentTask
from libtv_agent.services.analysis_lifecycle import (
    DURABLE_ANALYSIS_CLAIMED,
    DURABLE_ANALYSIS_QUEUED,
    recover_stale_analyses,
)
from sqlalchemy import and_, or_, select, update

logger = logging.getLogger(__name__)


async def claim_next(session, *, stale_seconds: float, now=None):
    now = now or datetime.now(UTC)
    eligible = or_(
        ParentTask.analysis_stage == DURABLE_ANALYSIS_QUEUED,
        and_(
            ParentTask.analysis_stage == DURABLE_ANALYSIS_CLAIMED,
            ParentTask.analysis_heartbeat_at < now - timedelta(seconds=stale_seconds),
        ),
    )
    # Only pre-provider claims can be retried; never adopt legacy/automatic jobs.
    # One UPDATE with a scalar subquery avoids a read-to-write SQLite upgrade.
    candidate = (
        select(ParentTask.id)
        .where(ParentTask.phase == ParentTaskPhase.ANALYZING, eligible)
        .order_by(ParentTask.analysis_started_at, ParentTask.id)
        .limit(1)
        .scalar_subquery()
    )
    run_id = uuid.uuid4()
    row = (
        await session.execute(
            update(ParentTask)
            .where(
                ParentTask.id == candidate, ParentTask.phase == ParentTaskPhase.ANALYZING, eligible
            )
            .values(
                analysis_stage=DURABLE_ANALYSIS_CLAIMED,
                analysis_run_id=run_id,
                analysis_heartbeat_at=now,
                updated_at=now,
            )
            .returning(ParentTask.id, ParentTask.user_id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    await session.commit()
    return (row[0], row[1], run_id) if row else None


async def worker_loop(database, settings, object_store, kling_adapter, stopping):
    # Import lazily to reuse exactly the existing execution/error contract.
    from libtv_agent.api.tasks import _run_analysis_job

    app = SimpleNamespace(
        state=SimpleNamespace(
            database=database,
            settings=settings,
            object_store=object_store,
            kling_adapter=kling_adapter,
        )
    )
    while not stopping.is_set():
        try:
            async with database.session_factory() as session:
                claimed = await claim_next(
                    session, stale_seconds=settings.analysis_stale_after_seconds
                )
            if claimed:
                await _run_analysis_job(app, *claimed)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("durable manual analysis worker cycle failed")
        await asyncio.sleep(max(0.2, settings.runner_poll_seconds))


async def recovery_loop(database, settings, stopping):
    while not stopping.is_set():
        try:
            async with database.session_factory() as session:
                await recover_stale_analyses(session, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("durable analysis recovery failed")
        remaining = settings.analysis_recovery_poll_seconds
        while remaining > 0 and not stopping.is_set():
            interval = min(remaining, 1.0)
            await asyncio.sleep(interval)
            remaining -= interval
