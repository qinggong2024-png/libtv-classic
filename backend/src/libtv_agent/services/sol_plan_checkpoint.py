"""Pre-paid Sol plan checkpoints. Never replay or create a paid video command here.

A checkpoint is a *validated compiled AI response*, not a completed generation
attempt. It is written only while the original worker owns the planning lease.
On restart the same payload/facts/route may reuse it; a different request must
run the planner normally. Final generation creation retains its own CAS fence.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import GenerationVariant, ParentTask
from libtv_agent.services.ai_creative_planner import CompiledCreativeVariant
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

CHECKPOINT_KEY = "_prepaidSolPlanCheckpoint"
SCHEMA_VERSION = 1
_BUSY_RETRY_DELAYS = (0.15, 0.4, 0.9)


def _sqlite_busy(error: OperationalError) -> bool:
    cause = error.orig
    code = getattr(cause, "sqlite_errorcode", None)
    return (
        code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        or "database is locked" in str(cause).casefold()
        or "database table is locked" in str(cause).casefold()
    )


def restored_plan(
    task: ParentTask | None, *, fingerprint: str, variant_count: int,
) -> tuple[CompiledCreativeVariant, ...] | None:
    """Fail closed on a stale, partial or malformed checkpoint."""
    if task is None or task.phase != ParentTaskPhase.ANALYZING:
        return None
    raw = (task.compiled_plan_json or {}).get(CHECKPOINT_KEY)
    if (
        not isinstance(raw, dict)
        or raw.get("version") != SCHEMA_VERSION
        or raw.get("fingerprint") != fingerprint
        or not isinstance(raw.get("variants"), list)
        or len(raw["variants"]) != variant_count
    ):
        return None
    variants = []
    for item in raw["variants"]:
        if not isinstance(item, dict):
            return None
        fields = ("prompt", "creative_idea", "visual_hook", "buyer_reason",
                  "beats", "facts", "planner_version", "request_attempts")
        if set(item) != set(fields):
            return None
        if not all(isinstance(item[key], str) for key in
                   ("prompt", "creative_idea", "visual_hook", "buyer_reason",
                    "planner_version")):
            return None
        if not 0 < len(item["prompt"]) <= 3072:
            return None
        if type(item["request_attempts"]) is not int or not 1 <= item["request_attempts"] <= 5:
            return None
        if not isinstance(item["beats"], list) or not isinstance(item["facts"], list):
            return None
        if not item["facts"] or not all(
            isinstance(fact, dict) and type(fact.get("person_count_verified")) is bool
            for fact in item["facts"]
        ):
            return None
        if not all(isinstance(beat, dict) for beat in item["beats"]):
            return None
        variants.append(CompiledCreativeVariant(
            prompt=item["prompt"], creative_idea=item["creative_idea"],
            visual_hook=item["visual_hook"], buyer_reason=item["buyer_reason"],
            beats=tuple(item["beats"]), facts=tuple(item["facts"]),
            planner_version=item["planner_version"],
            request_attempts=item["request_attempts"],
        ))
    return tuple(variants)


async def save_plan_checkpoint(
    session: AsyncSession, *,
    task_id: uuid.UUID, run_id: uuid.UUID, fingerprint: str,
    variants: tuple[CompiledCreativeVariant, ...] | list[CompiledCreativeVariant],
) -> bool:
    """CAS-write a plan, never after cancellation, take-over, or paid handoff."""
    if not variants:
        return False
    # The final task initialization replaces compiled_plan_json with the normal
    # production plan after claiming PREPARING. The checkpoint lives only in
    # the pre-paid ANALYZING phase.
    checkpoint = {
        CHECKPOINT_KEY: {
            "version": SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "variants": [asdict(variant) for variant in variants],
        }
    }
    for attempt in range(len(_BUSY_RETRY_DELAYS) + 1):
        try:
            result = await session.execute(
                update(ParentTask).where(
                    ParentTask.id == task_id,
                    ParentTask.phase == ParentTaskPhase.ANALYZING,
                    ParentTask.analysis_stage == "RUNNING",
                    ParentTask.analysis_run_id == run_id,
                    ~select(GenerationVariant.id).where(
                        GenerationVariant.parent_task_id == task_id,
                    ).exists(),
                ).values(
                    compiled_plan_json=checkpoint,
                    analysis_heartbeat_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return False
            await session.commit()
            return True
        except OperationalError as error:
            await session.rollback()
            if not _sqlite_busy(error) or attempt == len(_BUSY_RETRY_DELAYS):
                raise
            # Only a local, idempotent, CAS-protected database write is retried.
            # Never retry a whole task or an external paid-provider invocation.
            await asyncio.sleep(_BUSY_RETRY_DELAYS[attempt])
    return False
