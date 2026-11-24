"""Opt-in single-task confirmation outbox. Never replays a started prepare.

Queued events and HTTP receipts commit atomically. A started event is a
one-shot execution boundary, not a retryable lease. Reconciliation requires
the old Runner to have exited; do not run two live Classic Runners.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from libtv_agent.domain.enums import ConsistencyLevel, OutboxStatus, ParentTaskPhase
from libtv_agent.domain.models import (
    GenerationAttempt,
    GenerationCommand,
    GenerationVariant,
    OutboxEvent,
    ParentTask,
    TaskConfirmationVersion,
    User,
)
from libtv_agent.domain.schemas import ConfirmRequest
from libtv_agent.services.confirmation import confirm_task
from libtv_agent.services.tasks import get_task
from sqlalchemy import func, select, update

logger = logging.getLogger(__name__)
REQUESTED = "DURABLE_CONFIRMATION_REQUESTED"
STARTED = "DURABLE_CONFIRMATION_STARTED"
FINISHED = "DURABLE_CONFIRMATION_FINISHED"
REVIEW = "DURABLE_CONFIRMATION_REVIEW"
QUEUED = "DURABLE_CONFIRM_QUEUED"
RUNNING = "DURABLE_CONFIRM_RUNNING"


async def enqueue_confirmation(session, task, actor_id, payload: ConfirmRequest):
    available = {variant.variant_type for variant in task.variants}
    if not available or (payload.variants and not set(payload.variants).issubset(available)):
        raise ValueError("selected variants are unavailable")
    if task.consistency_level == ConsistencyLevel.SYSTEM_BLOCK:
        raise ValueError("SYSTEM_BLOCK tasks cannot be confirmed")
    if task.consistency_level == ConsistencyLevel.QUALITY_REVIEW and not payload.accept_risk:
        raise ValueError("acceptRisk is required for QUALITY_REVIEW")
    baseline_revision = (
        await session.scalar(
            select(func.max(TaskConfirmationVersion.revision)).where(
                TaskConfirmationVersion.parent_task_id == task.id
            )
        )
        or 0
    )
    version = payload.expected_state_version + 1
    changed = await session.execute(
        update(ParentTask)
        .where(
            ParentTask.id == task.id,
            ParentTask.user_id == actor_id,
            ParentTask.phase == ParentTaskPhase.AWAITING_CONFIRMATION,
            ParentTask.state_version == payload.expected_state_version,
        )
        .values(
            phase=ParentTaskPhase.PREPARING,
            phase_reason_code=QUEUED,
            state_version=version,
            updated_at=datetime.now(UTC),
        )
    )
    if changed.rowcount != 1:
        raise ValueError("task state changed")
    session.add(
        OutboxEvent(
            aggregate_type="parent_task",
            aggregate_id=str(task.id),
            event_type=REQUESTED,
            status=OutboxStatus.PENDING,
            payload_json={
                "actorId": str(actor_id),
                "stateVersion": version,
                "baselineRevision": baseline_revision,
                "acceptRisk": payload.accept_risk,
                "variants": [v.value for v in payload.variants] if payload.variants else None,
            },
        )
    )
    await session.flush()


async def claim_confirmation(session):
    eligible = (OutboxEvent.event_type == REQUESTED, OutboxEvent.status == OutboxStatus.PENDING)
    candidate = (
        select(OutboxEvent.id)
        .where(*eligible)
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(1)
        .scalar_subquery()
    )
    row = (
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == candidate, *eligible)
            .values(
                event_type=STARTED,
                status=OutboxStatus.PUBLISHED,
                published_at=datetime.now(UTC),
                attempt_count=OutboxEvent.attempt_count + 1,
            )
            .returning(OutboxEvent.id, OutboxEvent.aggregate_id, OutboxEvent.payload_json)
        )
    ).first()
    if row is None:
        await session.commit()
        return None
    event_id, task_id, payload = row[0], uuid.UUID(row[1]), row[2]
    changed = await session.execute(
        update(ParentTask)
        .where(
            ParentTask.id == task_id,
            ParentTask.user_id == uuid.UUID(payload["actorId"]),
            ParentTask.phase == ParentTaskPhase.PREPARING,
            ParentTask.phase_reason_code == QUEUED,
            ParentTask.state_version == payload["stateVersion"],
        )
        .values(phase_reason_code=RUNNING, updated_at=datetime.now(UTC))
    )
    if changed.rowcount != 1:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(event_type=REVIEW, status=OutboxStatus.FAILED)
        )
        await session.commit()
        return None
    await session.commit()
    return event_id, task_id, payload


async def settle_confirmation(session, event_id, *, error=None):
    event = await session.get(OutboxEvent, event_id)
    if event is None or event.event_type != STARTED:
        return
    task_id = uuid.UUID(event.aggregate_id)
    task = await session.get(ParentTask, task_id)
    confirmed = await session.scalar(
        select(TaskConfirmationVersion.id)
        .where(
            TaskConfirmationVersion.parent_task_id == task_id,
            TaskConfirmationVersion.revision > event.payload_json["baselineRevision"],
            TaskConfirmationVersion.confirmed_by == uuid.UUID(event.payload_json["actorId"]),
        )
        .limit(1)
    )
    if confirmed or (task and task.phase == ParentTaskPhase.WAITING_FOR_SEAL):
        event.event_type = FINISHED
    else:
        event.event_type = REVIEW
        event.status = OutboxStatus.FAILED
        event.payload_json = {**event.payload_json, "reviewReason": error or "RESULT_UNKNOWN"}
        command = await session.scalar(
            select(GenerationCommand.id)
            .join(GenerationAttempt, GenerationAttempt.id == GenerationCommand.attempt_id)
            .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
            .where(GenerationVariant.parent_task_id == task_id)
            .limit(1)
        )
        # Preserve any existing command/charge evidence. Never retry a started event.
        if (
            task
            and not command
            and task.phase in (ParentTaskPhase.PREPARING, ParentTaskPhase.QUEUED)
        ):
            task.phase = ParentTaskPhase.ACTION_REQUIRED
            task.phase_reason_code = "DURABLE_CONFIRM_REVIEW"
            task.state_version += 1
    await session.commit()


async def reconcile_abandoned_confirmations(database):
    """Only call after acquiring the lifetime singleton Runner lock.

    Completed DB evidence closes the outbox; ambiguous preparations are marked
    for review without calling a provider or creating a generation command.
    """
    async with database.session_factory() as session:
        ids = list(
            await session.scalars(select(OutboxEvent.id).where(OutboxEvent.event_type == STARTED))
        )
    for event_id in ids:
        async with database.session_factory() as session:
            await settle_confirmation(session, event_id, error="RUNNER_RESTARTED")
    return len(ids)


async def execute_confirmation(database, settings, adapter, object_store, kling_adapter, claim):
    event_id, task_id, payload = claim
    actor_id = uuid.UUID(payload["actorId"])
    try:
        async with database.session_factory() as session:
            user = await session.get(User, actor_id)
            if user is None or not user.is_active:
                raise ValueError("confirmation owner is inactive")
            task = await get_task(session, actor_id, task_id)
            request = ConfirmRequest.model_validate(
                {
                    "expectedStateVersion": payload["stateVersion"],
                    "acceptRisk": payload["acceptRisk"],
                    "variants": payload["variants"],
                }
            )
            await confirm_task(
                session,
                adapter,
                object_store,
                task,
                actor_id,
                settings,
                kling_adapter=kling_adapter,
                accept_risk=request.accept_risk,
                expected_state_version=request.expected_state_version,
                selected_variant_kinds=request.variants,
                durable_preclaimed=True,
            )
    except asyncio.CancelledError:
        # Leave a durable STARTED record for explicit reconciliation, not replay.
        raise
    except Exception as exc:
        logger.exception("durable confirmation requires review")
        async with database.session_factory() as session:
            await settle_confirmation(session, event_id, error=type(exc).__name__)
    else:
        async with database.session_factory() as session:
            await settle_confirmation(session, event_id)


async def confirmation_worker_loop(
    database, settings, adapter, object_store, get_kling_adapter, stopping
):
    while not stopping.is_set():
        try:
            async with database.session_factory() as session:
                claim = await claim_confirmation(session)
            if claim:
                await execute_confirmation(
                    database, settings, adapter, object_store, get_kling_adapter(), claim
                )
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("durable confirmation worker cycle failed")
        await asyncio.sleep(max(0.2, settings.runner_poll_seconds))
