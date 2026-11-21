"""Isolated, read-only auto CUT status checks. No remote CUT or paid provider calls."""
import uuid

import pytest
from sqlalchemy import select

from libtv_agent.domain.models import AccountUsage, MultiCutflowTask, User
from libtv_agent.services.cutflow_auto_status import auto_dispatch_status
from libtv_agent.services.cutflow_auto_submit_v2 import enqueue_completed_v2

from test_cutflow_auto_submit_v2 import handoff_env, base_handoff_env, prepare


@pytest.mark.asyncio
async def test_eligible_task_reports_worker_offline_in_isolated_fixture(handoff_env):
    database, settings, _store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as session:
        status = await auto_dispatch_status(session, settings, ids["user"], ids["task"])
        assert status["state"] == "WORKER_UNAVAILABLE"
        assert status["batchId"] is None
        assert status["requiredUsd"] is None
        assert (await session.scalars(select(MultiCutflowTask))).all() == []


@pytest.mark.asyncio
async def test_budget_block_is_readonly_and_reports_actual_cost(handoff_env):
    database, settings, _store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as session:
        user = await session.get(User, ids["user"])
        user.daily_budget_microusd = 100_000
        await session.commit()
        status = await auto_dispatch_status(session, settings, ids["user"], ids["task"])
        assert status["state"] == "BLOCKED"
        assert status["reasonCode"] == "USD_DAILY_BUDGET_EXCEEDED"
        assert status["requiredUsd"] == 0.15
        assert status["remainingUsd"] == 0.1
        assert (await session.scalars(select(MultiCutflowTask))).all() == []
        assert (await session.scalars(select(AccountUsage))).all() == []


@pytest.mark.asyncio
async def test_existing_record_takes_precedence_and_account_scope_is_enforced(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    assert await enqueue_completed_v2(database, settings, store) == 1
    async with database.session_factory() as session:
        user = await session.get(User, ids["user"])
        user.daily_budget_microusd = 0
        await session.commit()
        status = await auto_dispatch_status(session, settings, ids["user"], ids["task"])
        assert status["state"] == "RECORDED"
        assert status["batchId"] is not None
        assert await auto_dispatch_status(session, settings, uuid.uuid4(), ids["task"]) is None
