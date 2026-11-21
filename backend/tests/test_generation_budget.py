import uuid
from datetime import timedelta

from libtv_agent.domain.enums import VariantKind
from libtv_agent.domain.models import AccountUsage, GenerationVariant, ParentTask, User
from libtv_agent.services.points import business_date
from sqlalchemy import func, select
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor
from test_global_usage_pricing import RATES, save


async def test_own_budget_isolated_even_for_admin_and_brand_manager(account_app):
    app, client, ids = account_app
    await save(client, ids)
    today = business_date()
    async with app.state.database.session_factory() as session:
        for index, name in enumerate(("root", "manager", "member", "other"), 1):
            user = await session.get(User, ids[name])
            user.daily_budget_microusd = index * 10_000_000
            for day, status, amount in (
                (today, "ESTIMATED", index * 100_001),
                (today, "RESERVED", 200_000),
                (today, "UNCERTAIN", 300_000),
                (today, "RESERVED", None),
                (today, "RELEASED", 9_000_000),
                (today, "RELEASED", None),
                (today - timedelta(days=1), "ESTIMATED", 8_000_000),
            ):
                session.add(AccountUsage(
                    user_id=user.id, kind="IMAGE", operation_id=uuid.uuid4(),
                    business_date=day, units=1, status=status, amount_microusd=amount,
                ))
        await session.commit()
    for index, name in enumerate(("root", "manager", "member", "other"), 1):
        response = await client.get(
            f"/api/v1/account/generation-budget?userId={ids['other']}", headers=actor(ids, name),
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "day": today.isoformat(), "currency": "USD", "dailyBudgetUsd": index * 10,
            "budgetUsedUsd": (index * 100_001 + 500_000) / 1_000_000,
            "unpricedCount": 1, "prices": {**RATES, "cutflowPriceUsd": None},
        }


async def test_budget_read_preserves_unlimited_zero_unknown_prices_and_database(account_app):
    app, client, ids = account_app
    path = "/api/v1/account/generation-budget"
    first = await client.get(path, headers=actor(ids, "member"))
    assert first.status_code == 200
    assert first.json()["dailyBudgetUsd"] is None
    assert first.json()["budgetUsedUsd"] == 0
    assert first.json()["unpricedCount"] == 0
    assert all(value is None for value in first.json()["prices"].values())
    async with app.state.database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AccountUsage)) == 0
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 0
        await session.commit()
    await save(client, ids, {**RATES, "imagePriceUsd": 0})
    second = (await client.get(path, headers=actor(ids, "member"))).json()
    assert second["dailyBudgetUsd"] == 0
    assert second["prices"]["imagePriceUsd"] == 0
    assert (await client.get(path)).status_code in (401, 403)


async def test_task_reads_expose_each_variants_saved_budget_seconds(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        task = ParentTask(
            user_id=ids["member"], client_task_id="budget-seconds", title="Budget seconds",
            asset_count=0, assets=[], variants=[
                GenerationVariant(variant_type=VariantKind.STABLE,
                                  generation_config_json={"duration": "6"}),
                GenerationVariant(variant_type=VariantKind.DYNAMIC,
                                  generation_config_json={"durationSeconds": 12}),
            ],
        )
        session.add(task)
        await session.commit()
        task_id = task.id
    response = await client.get(f"/api/v1/tasks/{task_id}", headers=actor(ids, "member"))
    assert response.status_code == 200, response.text
    assert {variant["kind"]: variant["budgetDurationSeconds"]
            for variant in response.json()["variants"]} == {"STABLE": 6, "DYNAMIC": 12}
