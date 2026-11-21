"""Kling CLI dollar valuation, real receipt settlement, and non-blocking credits."""

import uuid

import pytest
from fastapi import HTTPException
from libtv_agent.domain.enums import ChargeStatus
from libtv_agent.domain.models import AccountUsage, User
from libtv_agent.services.kling_cli_cost import credits_to_microusd, estimate_cli_credits
from libtv_agent.services.kling_cli_credits import cli_credit_warning
from libtv_agent.services.points import reserve_points, settle_points
from sqlalchemy import select
from test_account_access_budgets import account_app  # noqa: F401


def test_cli_default_3_omni_credit_quote_and_conversion():
    assert estimate_cli_credits("kling_official_cli", "kling-video-v3_0_omni", 10) == 120
    assert estimate_cli_credits("kling_official_cli", "kling-video-v3_0_omni", 15) == 180
    assert credits_to_microusd(80) == 868_000
    assert credits_to_microusd(120) == 1_302_000
    assert estimate_cli_credits("kling_official_api", "kling-video-v3_0_omni", 10) is None
    assert estimate_cli_credits("kling_official_cli", "other-model", 10) is None
    with pytest.raises(ValueError):
        credits_to_microusd(-1)


def test_credit_alert_is_read_only_and_no_admission_block():
    assert cli_credit_warning(9455, 700)["lowBalance"] is False
    low = cli_credit_warning(300, 3500)
    assert low["warningThresholdCredits"] == 1000
    assert low["lowBalance"] is True
    assert low["admissionBlocked"] is False
    empty = cli_credit_warning(300, 0)
    assert empty["estimatedDaysRemaining"] is None


@pytest.mark.asyncio
async def test_cli_usd_reservation_actual_reconciliation_and_release(account_app):  # noqa: F811 - imported pytest fixture
    app, client, ids = account_app
    user_id = ids["member"]
    app.state.settings.kling_provider_backend = "cli"
    app.state.settings.kling_cli_enabled = True
    async with app.state.database.session_factory() as session:
        user = await session.get(User, user_id)
        user.daily_budget_microusd = 2_000_000
        await session.commit()
        first_id = uuid.uuid4()
        first = await reserve_points(
            session,
            user_id=user_id,
            attempt_id=first_id,
            estimated_points=120,
            daily_limit=0,
            usd_units=10,
            usd_pricing_category="KLING_CLI",
            usd_amount_override_microusd=credits_to_microusd(120),
        )
        usage = await session.scalar(
            select(AccountUsage).where(AccountUsage.operation_id == first_id)
        )
        assert usage.amount_microusd == 1_302_000
        assert usage.units == 10
        await session.commit()

        # USD budget is the only local admission limit; daily_limit=0 is ignored.
        second_id = uuid.uuid4()
        with pytest.raises(HTTPException) as denied:
            await reserve_points(
                session,
                user_id=user_id,
                attempt_id=second_id,
                estimated_points=120,
                daily_limit=0,
                usd_units=10,
                usd_pricing_category="KLING_CLI",
                usd_amount_override_microusd=credits_to_microusd(120),
            )
        assert denied.value.status_code == 429
        await session.rollback()

        first = await session.scalar(select(type(first)).where(type(first).attempt_id == first_id))
        await settle_points(session, first, actual_points=80, charge_status=ChargeStatus.CHARGED)
        await session.commit()
        usage = await session.scalar(
            select(AccountUsage).where(AccountUsage.operation_id == first_id)
        )
        await session.refresh(usage)  # Bulk SQL settlement bypasses the ORM identity map.
        assert usage.status == "ESTIMATED"
        assert usage.amount_microusd == 868_000
        # Re-settlement cannot double count.
        await settle_points(session, first, actual_points=80, charge_status=ChargeStatus.CHARGED)
        await session.commit()
        assert usage.amount_microusd == 868_000

        second = await reserve_points(
            session,
            user_id=user_id,
            attempt_id=second_id,
            estimated_points=80,
            daily_limit=0,
            usd_units=10,
            usd_pricing_category="KLING_CLI",
            usd_amount_override_microusd=credits_to_microusd(80),
        )
        await settle_points(session, second, actual_points=None, charge_status=ChargeStatus.UNKNOWN)
        await session.commit()
        second_usage = await session.scalar(
            select(AccountUsage).where(AccountUsage.operation_id == second_id)
        )
        assert second_usage.status == "UNCERTAIN"
        assert second_usage.amount_microusd == 868_000
        await settle_points(
            session, second, actual_points=None, charge_status=ChargeStatus.NOT_CHARGED
        )
        await session.commit()
        await session.refresh(second_usage)
        assert second_usage.status == "RELEASED"
        assert second_usage.amount_microusd == 868_000


@pytest.mark.asyncio
async def test_budget_display_uses_cli_quote_only_when_cli_selected(account_app):  # noqa: F811 - imported pytest fixture
    app, client, ids = account_app
    app.state.settings.kling_provider_backend = "cli"
    app.state.settings.kling_cli_enabled = True
    response = await client.get(
        "/api/v1/account/generation-budget",
        headers={"X-User-Id": str(ids["member"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["prices"]["videoPricePerSecondUsd"] == pytest.approx(0.1302)


@pytest.mark.asyncio
async def test_admin_credit_warning_does_not_gate_and_is_admin_only(account_app):  # noqa: F811 - imported pytest fixture
    app, client, ids = account_app
    app.state.settings.kling_provider_backend = "cli"
    app.state.settings.kling_cli_enabled = True

    class FakeAdapter:
        async def account_credits(self):
            return 300

    app.state.kling_adapter = FakeAdapter()
    path = "/api/v1/admin/kling/credits"
    unauth = await client.get(path)
    assert unauth.status_code == 401
    forbidden = await client.get(path, headers={"X-User-Id": str(ids["member"])})
    assert forbidden.status_code == 403
    response = await client.get(path, headers={"X-User-Id": str(ids["root"])})
    assert response.status_code == 200, response.text
    assert response.json()["lowBalance"] is True
    assert response.json()["admissionBlocked"] is False

    class UnavailableAdapter:
        async def account_credits(self):
            from libtv_agent.adapters.kling_official_cli import KlingCliError

            raise KlingCliError("KLING_CLI_PROCESS_FAILED", "offline")

    app.state.kling_adapter = UnavailableAdapter()
    error = await client.get(path, headers={"X-User-Id": str(ids["root"])})
    assert error.status_code == 200
    assert error.json() == {"status": "unavailable", "admissionBlocked": False}
