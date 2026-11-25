"""Fault injection of direct admission, with offline provider and isolated DB."""

from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from libtv_agent.domain.enums import ParentTaskPhase, AttemptStatus
from libtv_agent.domain.models import GenerationAttempt, GenerationCommand, ParentTask, OutboxEvent
from libtv_agent.services import fixed_video_tasks
from test_ootd_integration import harness, ootd_engine  # noqa: F401

real_queue = fixed_video_tasks.queue_video_task


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("failure", ["budget", "none", "preview"])
async def test_direct_admission_commits_attempt_commands_and_phase_together(harness, monkeypatch, failure):
    h = harness
    task = await h.create()
    tid = task.id
    uid = h.owner.id
    assert task.phase == ParentTaskPhase.PREPARING
    assert await count(h.session, GenerationAttempt) == 0
    monkeypatch.setattr(fixed_video_tasks, "ensure_provider_submission_allowed", AsyncMock())

    async def reserve(session, **kwargs):
        # The attempt exists in this transaction but its parent is not yet queued.
        assert await count(session, GenerationAttempt) > 0
        assert (await session.get(ParentTask, tid)).phase == ParentTaskPhase.PREPARING
        if failure == "budget":
            raise HTTPException(409, detail={"code": "USD_DAILY_BUDGET_EXCEEDED", "message": "test"})
    monkeypatch.setattr(fixed_video_tasks, "reserve_points", reserve)
    monkeypatch.setattr(fixed_video_tasks, "reserve_auto_cutflow", AsyncMock())

    def preview(*args, **kwargs):
        assert not h.session.in_transaction()
        if failure == "preview":
            raise OSError("synthetic diagnostic file failure")
    monkeypatch.setattr(fixed_video_tasks, "write_preview_file", preview)
    result = await real_queue(h.session, task=task, user_id=uid, settings=h.settings,
                              kling_adapter=h.adapter, object_store=h.store)
    if failure == "budget":
        assert result.phase == ParentTaskPhase.ACTION_REQUIRED
        assert result.phase_reason_code == "USD_DAILY_BUDGET_EXCEEDED"
        assert await count(h.session, GenerationAttempt) == 0
        assert await count(h.session, GenerationCommand) == 0
    else:
        assert result.phase == ParentTaskPhase.QUEUED
        attempts = list(await h.session.scalars(select(GenerationAttempt)))
        assert attempts and all(a.status == AttemptStatus.DISPATCH_PENDING for a in attempts)
        assert await count(h.session, GenerationCommand) == len(attempts)
        # GenerationCommand is the queue; there is no unconsumed shadow event.
        assert await count(h.session, OutboxEvent) == 0
    h.adapter.submit.assert_not_called()

async def test_unexpected_budget_write_failure_does_not_leave_pending_transaction(harness, monkeypatch):
    h = harness
    task = await h.create()
    tid, uid = task.id, h.owner.id
    monkeypatch.setattr(fixed_video_tasks, "ensure_provider_submission_allowed", AsyncMock())
    async def fail(session, **kwargs):
        assert await count(session, GenerationAttempt) > 0
        raise RuntimeError("synthetic interruption after attempt flush")
    monkeypatch.setattr(fixed_video_tasks, "reserve_points", fail)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        await real_queue(h.session, task=task, user_id=uid, settings=h.settings,
                         kling_adapter=h.adapter, object_store=h.store)
    assert not h.session.in_transaction()
    # A caller committing a claim cleanup after failure cannot publish an attempt.
    await h.session.commit()
    assert await count(h.session, GenerationAttempt) == 0
    assert await count(h.session, GenerationCommand) == 0
    assert (await h.session.get(ParentTask, tid)).phase == ParentTaskPhase.PREPARING
    h.adapter.submit.assert_not_called()
