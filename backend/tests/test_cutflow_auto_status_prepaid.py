"""A valid prepaid CUT hold must not be diagnosed as a second budget charge."""

import uuid

import pytest
from sqlalchemy import select

from libtv_agent.domain.models import AccountUsage, ParentTask, User
from libtv_agent.services.cutflow_auto_status import auto_dispatch_status
from libtv_agent.services.points import business_date

from test_cutflow_auto_submit_v2 import handoff_env, base_handoff_env, prepare


@pytest.mark.asyncio
async def test_prepaid_cutflow_is_not_flagged_as_insufficient_remaining_budget(handoff_env):
    database, settings, _store, ids = handoff_env
    await prepare(handoff_env)
    held_id = uuid.uuid4()
    async with database.session_factory() as session:
        user = await session.get(User, ids["user"])
        user.daily_budget_microusd = 100_000  # Below already-held $0.15
        parent = await session.get(ParentTask, ids["task"])
        parent.settings_json = {
            **(parent.settings_json or {}),
            "autoCutflowBudgetOperationId": str(held_id),
        }
        session.add(AccountUsage(
            user_id=ids["user"], kind="CUTFLOW", operation_id=held_id,
            business_date=business_date(), units=1,
            amount_microusd=150_000, status="RESERVED",
        ))
        await session.commit()

        status = await auto_dispatch_status(session, settings, ids["user"], ids["task"])
        assert status["state"] == "WORKER_UNAVAILABLE"
        assert status["reasonCode"] is None
        assert status["requiredUsd"] is None

        held = await session.scalar(select(AccountUsage).where(
            AccountUsage.operation_id == held_id,
        ))
        held.status = "RELEASED"
        await session.commit()
        status = await auto_dispatch_status(session, settings, ids["user"], ids["task"])
        assert status["state"] == "BLOCKED"
        assert status["reasonCode"] == "USD_DAILY_BUDGET_EXCEEDED"
