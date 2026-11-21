import uuid

import pytest
from fastapi import HTTPException
from libtv_agent.domain.models import AccountUsage, RuntimeConfigVersion, User
from libtv_agent.services.account_access import reserve_usd
from libtv_agent.services.runtime_control import get_runtime_config, update_runtime_config
from libtv_agent.services.usage_pricing import PRICING_KEY
from sqlalchemy import func, select
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

PATH = "/api/v1/admin/usage-pricing"
RATES = {
    "imagePriceUsd": 0.04,
    "videoPricePerSecondUsd": 0.25,
    "depthVideoPricePerSecondUsd": 0.05,
    "recreateVideoPricePerSecondUsd": 0.2,
}


async def save(client, ids, rates=None, version=None):
    headers = actor(ids, "root")
    if version is None:
        version = (await client.get(PATH, headers=headers)).json()["version"]
    return await client.put(
        PATH,
        headers=headers,
        json={
            "version": version,
            **(RATES if rates is None else rates),
        },
    )


async def test_global_rates_apply_across_accounts_and_new_users(account_app):
    app, client, ids = account_app
    response = await save(client, ids)
    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in RATES} == RATES
    created = await client.post(
        "/api/v1/admin/users",
        headers=actor(ids, "root"),
        json={
            "username": "new-priced-member",
            "password": "synthetic-test-password-123",
        },
    )
    assert created.status_code == 201
    ids["new"] = uuid.UUID(created.json()["id"])
    async with app.state.database.session_factory() as session:
        for name in ("member", "other", "new"):
            user = await session.get(User, ids[name])
            user.daily_budget_microusd = 5_000_000
            user.image_price_microusd = 9_000_000  # Legacy per-account values are ignored.
            for kind, category, units, amount in (
                ("IMAGE", "IMAGE", 2, 80_000),
                ("VIDEO", "VIDEO", 4, 1_000_000),
                ("VIDEO", "DEPTH_VIDEO", 6, 300_000),
                ("VIDEO", "RECREATE_VIDEO", 6, 1_200_000),
            ):
                record = await reserve_usd(
                    session,
                    user_id=user.id,
                    kind=kind,
                    pricing_category=category,
                    operation_id=uuid.uuid4(),
                    units=units,
                )
                assert record.amount_microusd == amount
        await session.commit()
    response = await client.patch(
        f"/api/v1/admin/users/{ids['member']}/policy",
        headers=actor(ids, "root"),
        json={"dailyBudgetUsd": 2.58},
    )
    assert response.status_code == 200
    async with app.state.database.session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=ids["member"], kind="IMAGE", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"
    async with app.state.database.session_factory() as session:
        record = await reserve_usd(
            session, user_id=ids["other"], kind="IMAGE", operation_id=uuid.uuid4(), units=1
        )
        assert record.amount_microusd == 40_000


async def test_price_changes_keep_history_replays_and_other_runtime_settings(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        current = await get_runtime_config(session, app.state.settings)
        await update_runtime_config(
            session,
            app.state.settings,
            config={**current.config_json, "globalPaused": True},
            actor_id=ids["root"],
            reason="test safety pause",
        )
        await session.commit()
    first = await save(client, ids)
    operation_id = uuid.uuid4()
    async with app.state.database.session_factory() as session:
        record = await reserve_usd(
            session, user_id=ids["member"], kind="IMAGE", operation_id=operation_id, units=2
        )
        assert record.amount_microusd == 80_000
        await session.commit()
    assert (await save(client, ids, {**RATES, "imagePriceUsd": 0.1})).status_code == 200
    stale = await save(client, ids, version=first.json()["version"])
    assert stale.status_code == 409
    async with app.state.database.session_factory() as session:
        current = await get_runtime_config(session, app.state.settings)
        assert current.config_json["globalPaused"] is True
        assert current.config_json[PRICING_KEY]["imagePriceUsd"] == 100_000
        replay = await reserve_usd(
            session, user_id=ids["member"], kind="IMAGE", operation_id=operation_id, units=2
        )
        assert replay.amount_microusd == 80_000
        new = await reserve_usd(
            session, user_id=ids["member"], kind="IMAGE", operation_id=uuid.uuid4(), units=2
        )
        assert new.amount_microusd == 200_000
        assert await session.scalar(select(func.count()).select_from(AccountUsage)) == 2


async def test_legacy_consensus_conflicts_and_explicit_null(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        a, b = await session.get(User, ids["member"]), await session.get(User, ids["other"])
        a.image_price_microusd = b.image_price_microusd = 40_000
        a.video_price_microusd, b.video_price_microusd = 100_000, 200_000
        a.daily_budget_microusd = b.daily_budget_microusd = 10_000_000
        await session.commit()
    initial = (await client.get(PATH, headers=actor(ids, "root"))).json()
    assert initial["imagePriceUsd"] == 0.04
    assert initial["videoPricePerSecondUsd"] is None
    assert initial["legacyConflicts"] == ["videoPricePerSecondUsd"]
    async with app.state.database.session_factory() as session:
        # GET must not bootstrap database configuration.
        assert await session.scalar(select(func.count()).select_from(RuntimeConfigVersion)) == 0
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=ids["member"], kind="VIDEO", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.detail["code"] == "USD_PRICE_REQUIRED"
    assert (await save(client, ids, {**RATES, "imagePriceUsd": None})).status_code == 200
    async with app.state.database.session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=ids["member"], kind="IMAGE", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.detail["code"] == "USD_PRICE_REQUIRED"
    assert (await save(client, ids, {**RATES, "imagePriceUsd": 0})).status_code == 200
    async with app.state.database.session_factory() as session:
        record = await reserve_usd(
            session, user_id=ids["member"], kind="IMAGE", operation_id=uuid.uuid4(), units=1
        )
        assert record.amount_microusd == 0


async def test_admin_only_validation_and_account_policy_cannot_change_rates(account_app):
    _, client, ids = account_app
    for name in ("member", "manager", "other"):
        assert (await client.get(PATH, headers=actor(ids, name))).status_code == 403
        assert (
            await client.put(PATH, headers=actor(ids, name), json={"version": 0, **RATES})
        ).status_code == 403
    for value in (-1, 10_001, "0.0000001", "NaN", "Infinity"):
        assert (await save(client, ids, {**RATES, "imagePriceUsd": value})).status_code == 422
    for key, value in RATES.items():
        response = await client.patch(
            f"/api/v1/admin/users/{ids['member']}/policy",
            headers=actor(ids, "root"),
            json={key: value},
        )
        assert response.status_code == 422
