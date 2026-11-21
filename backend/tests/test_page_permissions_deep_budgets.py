import importlib.util
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from libtv_agent.domain.models import AccountUsage, User
from libtv_agent.services.account_access import reserve_usd, settle_usd
from libtv_agent.services.points import business_date
from sqlalchemy import select
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor


@pytest.mark.parametrize("pages", [[], ["images"], ["make"], ["records"], ["library"]])
async def test_page_grants_enforce_api_access(account_app, pages):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        member = await session.get(User, ids["member"])
        member.page_permissions_json = pages
        await session.commit()
    headers = actor(ids, "member")
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["pagePermissions"] == [page for page in pages if page != "library"]
    assert response.json()["canAccessManagement"] is False
    assert (await client.get("/api/v1/account/usage", headers=headers)).status_code == 200
    for path, allowed, success in (
        ("/api/v1/tasks", bool(set(pages) & {"make", "records"}), 200),
        ("/api/v1/video-tasks/deep/status", "make" in pages, 200),
        (f"/api/v1/image-generation-batches/{uuid.uuid4()}", "images" in pages, 404),
        ("/api/v1/motion-actions", "make" in pages, 200),
        ("/api/v1/cutflow-handoffs/sites", bool(set(pages) & {"images", "make", "records"}), 200),
        ("/api/v1/cutflow-handoffs", bool(set(pages) & {"make", "records"}), 200),
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == (success if allowed else 403), response.text
    if "make" not in pages:
        for path in ("/api/v1/video-tasks", "/api/v1/video-tasks/deep",
                     "/api/v1/uploads/video", "/api/v1/confirmations/execute"):
            response = await client.post(path, headers=headers, json={})
            assert response.status_code == 403, response.text
    response = await client.post("/api/v1/motion-actions", headers=headers, json={})
    assert response.status_code == 403


async def test_admin_name_alone_or_role_alone_does_not_open_management(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        admin = await session.get(User, ids["root"])
        admin.page_permissions_json = []
        await session.commit()
    response = await client.get("/api/v1/auth/me", headers=actor(ids, "root"))
    assert response.json()["canAccessManagement"] is True
    assert len(response.json()["pagePermissions"]) == 4
    async with app.state.database.session_factory() as session:
        admin = await session.get(User, ids["root"])
        admin.role = "USER"
        await session.commit()
    response = await client.get("/api/v1/admin/users", headers=actor(ids, "root"))
    assert response.status_code == 403


async def test_other_admin_events_are_personal_and_history_has_no_invented_category(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["other"])
        user.role = "ADMIN"
        session.add(AccountUsage(
            user_id=user.id, kind="VIDEO", operation_id=uuid.uuid4(),
            business_date=business_date(), units=1,
            amount_microusd=123_000, status="ESTIMATED",
        ))
        await session.commit()
    response = await client.get("/api/v1/events?once=true", headers=actor(ids, "other"))
    assert response.status_code == 200
    assert "event: tasks.snapshot" in response.text
    assert "operations.snapshot" not in response.text
    report = await client.get("/api/v1/account/usage", headers=actor(ids, "other"))
    assert report.json()["items"][0]["category"] is None
    assert report.json()["items"][0]["amountUsd"] == .123


async def test_distinct_rates_share_total_and_preserve_snapshots(account_app):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 3_320_000
        user.image_price_microusd = 10_000
        user.video_price_microusd = 100_000
        user.depth_video_price_microusd = 200_000
        user.recreate_video_price_microusd = 300_000
        records = []
        for kind, category, units, amount in (
            ("IMAGE", "IMAGE", 2, 20_000),
            ("VIDEO", "VIDEO", 3, 300_000),
            ("VIDEO", "DEPTH_VIDEO", 6, 1_200_000),
            ("VIDEO", "RECREATE_VIDEO", 6, 1_800_000),
        ):
            operation_id = uuid.uuid4()
            record = await reserve_usd(
                session, user_id=user.id, kind=kind, pricing_category=category,
                operation_id=operation_id, units=units,
            )
            assert record.amount_microusd == amount
            replay = await reserve_usd(
                session, user_id=user.id, kind=kind, pricing_category=category,
                operation_id=operation_id, units=units,
            )
            assert replay.id == record.id
            records.append(record)
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=user.id, kind="VIDEO", pricing_category="DEPTH_VIDEO",
                operation_id=uuid.uuid4(), units=1,
            )
        assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"
        user.recreate_video_price_microusd = 500_000
        await settle_usd(session, kind="VIDEO", operation_id=records[-1].operation_id,
                         status="UNCERTAIN")
        await session.refresh(records[-1])
        assert records[-1].amount_microusd == 1_800_000
        assert records[-1].pricing_category == "RECREATE_VIDEO"
        assert records[-1].status == "UNCERTAIN"
        assert len(list(await session.scalars(select(AccountUsage)))) == 4


@pytest.mark.parametrize("category", ["DEPTH_VIDEO", "RECREATE_VIDEO"])
async def test_deep_rate_missing_does_not_fall_back_to_regular_video(account_app, category):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 10_000_000
        user.video_price_microusd = 100_000
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=user.id, kind="VIDEO", pricing_category=category,
                operation_id=uuid.uuid4(), units=6,
            )
        assert error.value.detail["code"] == "USD_PRICE_REQUIRED"


def migration_tool():
    path = Path(__file__).resolve().parents[2] / "scripts/classic_access_migrate.py"
    spec = importlib.util.spec_from_file_location("page_migrate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incremental_migration_preserves_old_accounts_and_amounts(tmp_path):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE app_user (id TEXT PRIMARY KEY, username TEXT, password_hash TEXT, "
            "brand_id TEXT, is_brand_admin BOOLEAN, daily_budget_microusd BIGINT, "
            "image_price_microusd BIGINT, video_price_microusd BIGINT)"
        )
        connection.execute("CREATE TABLE brand (id TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE account_usage (id TEXT PRIMARY KEY, units INTEGER, "
            "amount_microusd BIGINT)"
        )
        connection.execute(
            "INSERT INTO app_user VALUES ('keep-id','classic-admin','keep-current-hash',"
            "'keep-brand',1,5000000,40000,100000)"
        )
        connection.execute("INSERT INTO account_usage VALUES ('old-operation',6,123456)")
    module = migration_tool()
    assert module.upgrade_database(path)["status"] == "pending"
    result = module.upgrade_database(path, apply=True)
    assert Path(result["backup"]).is_file()
    assert module.upgrade_database(path, apply=True)["status"] == "already_current"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM app_user").fetchone() == (
            "keep-id", "classic-admin", "keep-current-hash", "keep-brand", 1,
            5000000, 40000, 100000, None, None, None,
        )
        assert connection.execute("SELECT * FROM account_usage").fetchone() == (
            "old-operation", 6, 123456, None,
        )


def test_partial_migration_is_rejected_without_altering_database(tmp_path):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE app_user (brand_id TEXT)")
    with pytest.raises(RuntimeError, match="Partial account schema"):
        migration_tool().upgrade_database(path, apply=True)


@pytest.mark.parametrize("role,brand_admin", [("USER", False), ("ADMIN", False), ("USER", True)])
@pytest.mark.parametrize("pages", [None, ["images", "make", "records", "library"]])
async def test_library_ignores_legacy_grants_for_nonmanagement_accounts(
    account_app, role, brand_admin, pages,
):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.role = role
        user.is_brand_admin = brand_admin
        user.page_permissions_json = pages
        await session.commit()
    headers = actor(ids, "member")
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["pagePermissions"] == ["images", "make", "records"]
    denied = await client.post("/api/v1/motion-actions", headers=headers, json={})
    assert denied.status_code == 403
    assert (await client.get("/api/v1/motion-actions", headers=headers)).status_code == 200
