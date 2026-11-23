import asyncio
import importlib.util
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole, VariantKind
from libtv_agent.domain.models import (
    AccountUsage,
    Base,
    Brand,
    GenerationVariant,
    ParentTask,
    PromptPreset,
    User,
)
from libtv_agent.services.account_access import reserve_usd, settle_usd
from libtv_agent.services.auth import PasswordService
from libtv_agent.services.content_catalog import (
    MOTION_ACTION,
    motion_action_library,
    save_catalog_entry,
)
from libtv_agent.services.points import reserve_points
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def account_app(tmp_path):
    app = create_app(
        Settings(
            app_env="test",
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'accounts.db'}",
            artifact_root=tmp_path / "artifacts",
            runner_root=tmp_path / "runner",
            object_store_mode="local",
        )
    )
    engine = create_async_engine(app.state.settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    async with app.router.lifespan_context(app):
        ids = {name: uuid.uuid4() for name in ("root", "a", "b", "manager", "member", "other")}
        async with app.state.database.session_factory() as session:
            session.add_all([Brand(id=ids["a"], name="品牌 A"), Brand(id=ids["b"], name="品牌 B")])
            await session.flush()
            for name, brand, manager, role in (
                ("root", None, False, UserRole.ADMIN),
                ("manager", "a", True, UserRole.USER),
                ("member", "a", False, UserRole.USER),
                ("other", "b", False, UserRole.OPERATOR),
            ):
                session.add(
                    User(
                        id=ids[name],
                        username="classic-admin" if name == "root" else name,
                        password_hash="test-only-disabled",
                        brand_id=ids[brand] if brand else None,
                        is_brand_admin=manager,
                        role=role,
                    )
                )
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield app, client, ids


def actor(ids, name):
    return {"X-User-Id": str(ids[name])}


async def test_only_classic_admin_manages_accounts(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["other"])
        user.role = UserRole.ADMIN
        await session.commit()
    for name in ("manager", "member", "other"):
        for path in ("/api/v1/admin/users", "/api/v1/admin/brands",
                     "/api/v1/admin/operations", "/api/v1/admin/kling/elements"):
            response = await client.get(path, headers=actor(ids, name))
            assert response.status_code == 403, response.text
        response = await client.patch(
            f"/api/v1/admin/users/{ids['member']}/policy",
            headers=actor(ids, name), json={"dailyBudgetUsd": 100},
        )
        assert response.status_code == 403
    headers = actor(ids, "root")
    response = await client.get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 4
    response = await client.patch(
        f"/api/v1/admin/users/{ids['member']}/policy", headers=headers,
        json={"dailyBudgetUsd": "1.234567", "pagePermissions": ["images", "records"]},
    )
    assert response.status_code == 200, response.text
    policy = response.json()
    assert policy["dailyBudgetUsd"] == 1.234567
    assert policy["pagePermissions"] == ["images", "records"]
    for pages in (["admin"], ["unknown"], None):
        response = await client.patch(
            f"/api/v1/admin/users/{ids['member']}/policy", headers=headers,
            json={"pagePermissions": pages},
        )
        assert response.status_code == 422
    created = await client.post(
        "/api/v1/admin/users", headers=headers,
        json={"username": "new-member", "password": "test-password-only-123",
              "brandId": str(ids["a"]), "pagePermissions": ["library"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["pagePermissions"] == []  # Content library is classic-admin only.


async def test_second_platform_admin_has_management_and_remains_protected(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["other"])
        user.username = "classic-admin-2"
        user.role = UserRole.ADMIN
        await session.commit()
    headers = actor(ids, "other")
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["canAccessManagement"] is True
    assert "library" in me.json()["pagePermissions"]
    for path in ("/api/v1/admin/users", "/api/v1/admin/brands", "/api/v1/admin/operations"):
        assert (await client.get(path, headers=headers)).status_code == 200
    changed = await client.patch(
        f"/api/v1/admin/users/{ids['member']}/policy", headers=headers,
        json={"dailyBudgetUsd": 6},
    )
    assert changed.status_code == 200
    assert changed.json()["dailyBudgetUsd"] == 6
    for source, target in (("root", "other"), ("other", "root")):
        denied = await client.delete(
            f"/api/v1/admin/users/{ids[target]}", headers=actor(ids, source),
        )
        assert denied.status_code == 409
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["other"])
        user.role = UserRole.USER
        await session.commit()
    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403


@pytest.mark.parametrize("role", ["USER", "TRUSTED_USER", "OPERATOR", "AUDITOR"])
async def test_new_accounts_default_to_five_dollar_daily_budget(account_app, role):
    _, client, ids = account_app
    created = await client.post(
        "/api/v1/admin/users",
        headers=actor(ids, "root"),
        json={"username": "budget-default", "password": "test-password-only-123", "role": role},
    )
    assert created.status_code == 201, created.text
    assert created.json()["dailyBudgetUsd"] == 5
    users = await client.get("/api/v1/admin/users", headers=actor(ids, "root"))
    policies = {user["username"]: user["dailyBudgetUsd"] for user in users.json()}
    assert policies["budget-default"] == 5
    assert policies["member"] is None


async def test_brand_can_be_corrected_when_only_auto_seeded_presets_exist(account_app):
    app, client, ids = account_app
    admin = actor(ids, "root")
    created = await client.post(
        "/api/v1/admin/users", headers=admin,
        json={
            "username": "Y2K", "password": "test-password-only-123",
            "brandId": str(ids["a"]),
        },
    )
    assert created.status_code == 201, created.text
    user_id = uuid.UUID(created.json()["id"])
    async with app.state.database.session_factory() as session:
        presets = list(await session.scalars(
            select(PromptPreset).where(PromptPreset.user_id == user_id)
        ))
        assert len(presets) == 4
    response = await client.patch(
        f"/api/v1/admin/users/{user_id}/policy", headers=admin,
        json={"brandId": str(ids["b"]), "dailyBudgetUsd": 50},
    )
    assert response.status_code == 200, response.text
    assert response.json()["brandId"] == str(ids["b"])
    assert response.json()["dailyBudgetUsd"] == 50
    async with app.state.database.session_factory() as session:
        assert len(list(await session.scalars(
            select(PromptPreset).where(PromptPreset.user_id == user_id)
        ))) == 4


async def test_brand_change_still_denied_for_custom_preset_or_paid_usage(account_app):
    app, client, ids = account_app
    admin = actor(ids, "root")
    for suffix, business in (("custom", False), ("usage", True)):
        created = await client.post(
            "/api/v1/admin/users", headers=admin,
            json={
                "username": "wrong-brand-" + suffix,
                "password": "test-password-only-123", "brandId": str(ids["a"]),
            },
        )
        assert created.status_code == 201, created.text
        user_id = uuid.UUID(created.json()["id"])
        async with app.state.database.session_factory() as session:
            if business:
                user = await session.get(User, user_id)
                user.image_price_microusd = 100_000
                await reserve_usd(
                    session, user_id=user_id, kind="IMAGE",
                    operation_id=uuid.uuid4(), units=1,
                )
            else:
                session.add(PromptPreset(
                    user_id=user_id, scope="video", name="custom",
                    content="Custom brand-dependent user instructions", sort_order=1,
                ))
            await session.commit()
        blocked = await client.patch(
            f"/api/v1/admin/users/{user_id}/policy",
            headers=admin, json={"brandId": str(ids["b"])},
        )
        assert blocked.status_code == 409, blocked.text
        async with app.state.database.session_factory() as session:
            assert (await session.get(User, user_id)).brand_id == ids["a"]


async def test_usage_scope_and_account_policy(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for name in ("member", "other"):
            user = await session.get(User, ids[name])
            user.image_price_microusd = 100_001
            await reserve_usd(
                session, user_id=user.id, kind="IMAGE", operation_id=uuid.uuid4(), units=2
            )
        await session.commit()
    report = await client.get("/api/v1/account/usage", headers=actor(ids, "manager"))
    assert report.status_code == 200
    assert report.json()["reservedUsd"] == 0
    assert report.json()["items"] == []
    assert (
        await client.get(
            f"/api/v1/account/usage?userId={ids['other']}", headers=actor(ids, "manager")
        )
    ).status_code == 404
    root = (await client.get("/api/v1/account/usage", headers=actor(ids, "root"))).json()
    assert root["total"] == 2
    assert root["reservedUsd"] == 0.400004
    assert (
        await client.get("/api/v1/admin/users", headers=actor(ids, "member"))
    ).status_code == 403
    invalid = await client.patch(
        f"/api/v1/admin/users/{ids['member']}/policy",
        headers=actor(ids, "root"),
        json={"dailyBudgetUsd": -1},
    )
    assert invalid.status_code == 422
    assert (
        await client.patch(
            f"/api/v1/admin/users/{ids['member']}/policy",
            headers=actor(ids, "root"),
            json={"brandId": str(ids["b"])},
        )
    ).status_code == 409


async def test_cross_brand_task_ids_and_reviews_stay_hidden(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        task = ParentTask(
            user_id=ids["other"],
            client_task_id="private-b",
            title="private-b",
            asset_count=0,
            assets=[],
            variants=[GenerationVariant(variant_type=VariantKind.STABLE)],
        )
        session.add(task)
        await session.commit()
        task_id, variant_id = task.id, task.variants[0].id
    assert (
        await client.get(f"/api/v1/tasks/{task_id}", headers=actor(ids, "member"))
    ).status_code == 404
    review = {
        "decision": "APPROVED",
        "productFidelityPass": True,
        "editingAssemblyPass": True,
        "humanCriticalPass": True,
        "humanReviewSeconds": 10,
        "expectedStateVersion": 1,
    }
    result = await client.put(
        f"/api/v1/tasks/{task_id}/variants/{variant_id}/final-approval",
        headers=actor(ids, "manager"),
        json=review,
    )
    assert result.status_code == 404
    assert (
        await client.get(
            "/api/v1/library/kling/elements/foreign-subject", headers=actor(ids, "manager")
        )
    ).status_code == 404
    response = await client.get("/api/v1/library/kling/elements", headers=actor(ids, "manager"))
    assert response.json()["items"] == []


@pytest.mark.parametrize("name", ["member", "manager", "unassigned"])
async def test_foreign_subject_cannot_be_reused_in_video_creation(account_app, name):
    app, client, ids = account_app
    # Resolve the storefront first so this test reaches the foreign-subject guard.
    async with app.state.database.session_factory() as session:
        brand = await session.get(Brand, ids["a"])
        brand.name = "GK"
        await session.commit()
    if name == "unassigned":
        ids[name] = uuid.uuid4()
        async with app.state.database.session_factory() as session:
            session.add(User(id=ids[name], username=name, password_hash="test-only"))
            await session.commit()
    response = await client.post(
        "/api/v1/video-tasks",
        headers={**actor(ids, name), "Idempotency-Key": f"foreign-subject-{name}"},
        json={
            "clientTaskId": f"foreign-subject-{name}",
            "title": "Scope check",
            "storyboards": [{"uploadId": str(uuid.uuid4()), "order": 1}],
            "elementIds": ["foreign-subject"],
            "templateId": "ootd_walk",
            "siteProfileId": "geeksoutfit",
            "paidSubmissionConsent": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ELEMENT_NOT_IN_BRAND"


async def test_sessions_csrf_and_access_changes(account_app):
    original, _, ids = account_app
    password = "synthetic-account-test-12345"
    async with original.state.database.session_factory() as session:
        for name in ("root", "manager", "member"):
            user = await session.get(User, ids[name])
            user.password_hash = PasswordService().hash(password)
        await session.commit()
    settings = original.state.settings.model_copy(
        update={
            "shared_authentication_enabled": True,
            "session_secret": "synthetic-session-secret-at-least-thirty-two-characters",
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="https://test") as root,
            AsyncClient(transport=transport, base_url="https://test") as member,
        ):
            admin_login = await root.post(
                "/api/v1/auth/login", json={"username": "classic-admin", "password": password}
            )
            csrf = admin_login.json()["csrfToken"]
            await member.post(
                "/api/v1/auth/login", json={"username": "member", "password": password}
            )
            # A forged offline identity cannot bypass a shared session's brand/role.
            assert (
                await member.get("/api/v1/admin/users", headers=actor(ids, "root"))
            ).status_code == 403
            path = f"/api/v1/admin/users/{ids['member']}/policy"
            assert (await root.patch(path, json={"dailyBudgetUsd": 5})).status_code == 403
            headers = {"X-CSRF-Token": csrf}
            pricing_path = "/api/v1/admin/usage-pricing"
            pricing = (await root.get(pricing_path)).json()
            pricing.pop("legacyConflicts")
            pricing["imagePriceUsd"] = .04
            assert (await root.put(pricing_path, json=pricing)).status_code == 403
            assert (await member.put(pricing_path, json=pricing)).status_code == 403
            assert (await root.put(pricing_path, headers=headers, json=pricing)).status_code == 200
            assert (await member.get("/api/v1/auth/me")).status_code == 200
            # Budget changes leave sessions intact; access changes revoke them.
            assert (
                await root.patch(path, headers=headers, json={"dailyBudgetUsd": 5})
            ).status_code == 200
            assert (await member.get("/api/v1/auth/me")).status_code == 200
            assert (
                await root.patch(path, headers=headers, json={"pagePermissions": ["images"]})
            ).status_code == 200
            assert (await member.get("/api/v1/auth/me")).status_code == 401
            await member.post(
                "/api/v1/auth/login", json={"username": "member", "password": password}
            )
            assert (await member.get("/api/v1/auth/me")).json()["pagePermissions"] == ["images"]
            assert (
                await root.patch(path, headers=headers, json={"isActive": False})
            ).status_code == 200
            assert (await member.get("/api/v1/auth/me")).status_code == 401


async def test_video_admission_uses_only_usd_even_when_legacy_points_are_exhausted(account_app):
    """A raised account USD budget unlocks new reservations without resetting legacy points."""
    from test_global_usage_pricing import save

    app, client, ids = account_app
    configured = await save(client, ids)
    assert configured.status_code == 200, configured.text
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 3_000_000
        await session.commit()

    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    async with app.state.database.session_factory() as session:
        first = await reserve_points(
            session, user_id=ids["member"], attempt_id=first_id,
            estimated_points=120, daily_limit=1, usd_units=10,
        )
        assert first.estimated_points == 120
        await session.commit()

    async with app.state.database.session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await reserve_points(
                session, user_id=ids["member"], attempt_id=second_id,
                estimated_points=120, daily_limit=1, usd_units=10,
            )
        assert error.value.status_code == 429
        assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"
        assert error.value.detail["details"] == {
            "limitUsd": 3.0, "usedUsd": 2.5, "requestedUsd": 2.5,
        }
        await session.rollback()

    updated = await client.patch(
        f"/api/v1/admin/users/{ids['member']}/policy",
        headers=actor(ids, "root"), json={"dailyBudgetUsd": 5},
    )
    assert updated.status_code == 200, updated.text
    async with app.state.database.session_factory() as session:
        second = await reserve_points(
            session, user_id=ids["member"], attempt_id=second_id,
            estimated_points=120, daily_limit=1, usd_units=10,
        )
        assert second.id != first.id
        await session.commit()
        amounts = list(await session.scalars(
            select(AccountUsage.amount_microusd).where(AccountUsage.user_id == ids["member"])
        ))
        assert sorted(amounts) == [2_500_000, 2_500_000]


async def test_batch_confirmation_preserves_usd_budget_error_code(account_app, monkeypatch):
    """A USD 429 is a per-task result, not an uncaught batch exception."""
    from libtv_agent.api import confirmations as confirmations_api

    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        task = ParentTask(
            user_id=ids["member"], client_task_id=str(uuid.uuid4()),
            title="USD batch rejection", asset_count=1,
        )
        session.add(task)
        await session.commit()
        task_id, version = task.id, task.state_version

    async def reject_usd(*_args, **_kwargs):
        raise HTTPException(
            429, detail={
                "code": "USD_DAILY_BUDGET_EXCEEDED",
                "message": "今日美元预算不足",
                "details": {"limitUsd": 3, "usedUsd": 2.5, "requestedUsd": 2.5},
            },
        )

    monkeypatch.setattr(confirmations_api, "confirm_task", reject_usd)
    response = await client.post(
        "/api/v1/confirmations/execute",
        headers={**actor(ids, "member"), "Idempotency-Key": str(uuid.uuid4())},
        json={"tasks": [{"taskId": str(task_id), "expectedStateVersion": version}]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["succeededCount"] == 0
    assert payload["failedCount"] == 1
    assert payload["results"][0]["errorCode"] == "USD_DAILY_BUDGET_EXCEEDED"
    assert payload["results"][0]["message"] == "今日美元预算不足"


async def test_budget_atomic_across_image_video_and_replay(account_app):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 1_000_000
        user.image_price_microusd = user.video_price_microusd = 600_000
        await session.commit()

    async def admit(kind):
        async with app.state.database.session_factory() as session:
            try:
                record = await reserve_usd(
                    session,
                    user_id=ids["member"],
                    kind=kind,
                    operation_id=uuid.uuid4(),
                    units=1,
                )
                await session.commit()
                return record.id
            except HTTPException as exc:
                await session.rollback()
                assert exc.status_code == 429
                return None

    results = await asyncio.gather(admit("IMAGE"), admit("VIDEO"))
    assert sum(v is not None for v in results) == 1
    async with app.state.database.session_factory() as session:
        record = await session.scalar(select(AccountUsage))
        replay = await reserve_usd(
            session,
            user_id=ids["member"],
            kind=record.kind,
            operation_id=record.operation_id,
            units=1,
        )
        assert replay.id == record.id
        await settle_usd(
            session, kind=record.kind, operation_id=record.operation_id, status="UNCERTAIN"
        )
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=ids["member"], kind="IMAGE", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"
        await settle_usd(
            session, kind=record.kind, operation_id=record.operation_id, status="RELEASED"
        )
        await reserve_usd(
            session, user_id=ids["member"], kind="VIDEO", operation_id=uuid.uuid4(), units=1
        )
        await session.commit()


async def test_video_budget_uses_duration_seconds(account_app):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 10_000_000
        user.video_price_microusd = 250_000
        record = await reserve_usd(
            session,
            user_id=user.id,
            kind="VIDEO",
            operation_id=uuid.uuid4(),
            units=12,
        )
        assert record.units == 12
        assert record.amount_microusd == 3_000_000


async def test_cutflow_budget_charges_per_source_and_fails_closed_when_unpriced(account_app):
    """剪辑和生图生视频共用每日美元预算：按原片条数计价，未定价时有预算的账号必须被拒。"""
    from libtv_agent.domain.models import RuntimeConfigVersion
    from libtv_agent.services.usage_pricing import PRICE_COLUMNS, PRICING_KEY

    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        member_id = user.id  # rollback 会让 ORM 对象过期，后续一律用纯值，避免异步惰性加载
        user.daily_budget_microusd = 10_000_000  # $10
        await session.commit()

        # 未配置剪辑单价：有预算的账号不能在无法核算的情况下先扣一笔
        with pytest.raises(HTTPException) as denied:
            await reserve_usd(
                session, user_id=member_id, kind="CUTFLOW",
                operation_id=uuid.uuid4(), units=2,
            )
        assert denied.value.status_code == 409
        assert denied.value.detail["code"] == "USD_PRICE_REQUIRED"
        await session.rollback()

        # 配上 0.15/条后，2 条应记 0.30
        latest = await session.scalar(
            select(func.coalesce(func.max(RuntimeConfigVersion.version), 0))
        )
        session.add(RuntimeConfigVersion(
            version=int(latest or 0) + 1,
            config_json={PRICING_KEY: {
                "imagePriceUsd": 50_000,
                "videoPricePerSecondUsd": 150_000,
                "depthVideoPricePerSecondUsd": 150_000,
                "recreateVideoPricePerSecondUsd": 200_000,
                "cutflowPriceUsd": 150_000,
            }},
            changed_by=None,
            change_reason="test: cutflow pricing",
        ))
        await session.commit()
        assert set(PRICE_COLUMNS) >= {"cutflowPriceUsd"}

        record = await reserve_usd(
            session, user_id=member_id, kind="CUTFLOW",
            operation_id=uuid.uuid4(), units=2,
        )
        assert record.units == 2
        assert record.pricing_category == "CUTFLOW"
        assert record.amount_microusd == 300_000

        # 同一 operation 重放不重复扣
        replay = await reserve_usd(
            session, user_id=member_id, kind="CUTFLOW",
            operation_id=record.operation_id, units=2,
        )
        assert replay.id == record.id


async def test_admin_can_record_reviewed_uncertain_budget_hold_without_finalizing_charge(
    account_app,
):
    """Unpriced history is recoverable without guessing provider charges or bypassing budget."""
    from libtv_agent.domain.models import AuditEvent
    from libtv_agent.services.points import business_date

    app, client, ids = account_app
    usage_id = uuid.uuid4()
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 500_000
        user.image_price_microusd = 100_000
        session.add(AccountUsage(
            id=usage_id, user_id=user.id, brand_id=user.brand_id,
            operation_id=uuid.uuid4(), business_date=business_date(),
            kind="VIDEO", pricing_category="VIDEO", units=2,
            amount_microusd=None, status="UNCERTAIN",
        ))
        await session.commit()
    async with app.state.database.session_factory() as session:
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=ids["member"], kind="IMAGE",
                operation_id=uuid.uuid4(), units=1,
            )
        assert error.value.detail["code"] == "USD_PRICE_REQUIRED"
        await session.rollback()

    payload = {
        "usageId": str(usage_id),
        "estimatedAmountUsd": "0.30",
        "reason": "Provider charge reviewed against statement",
    }
    url = "/api/v1/admin/account-usage/reconcile-unpriced"
    denied = await client.post(url, headers=actor(ids, "manager"), json=payload)
    assert denied.status_code == 403
    accepted = await client.post(url, headers=actor(ids, "root"), json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["finalProviderChargeConfirmed"] is False
    assert (await client.post(url, headers=actor(ids, "root"), json=payload)).status_code == 409

    async with app.state.database.session_factory() as session:
        usage = await session.get(AccountUsage, usage_id)
        assert usage.amount_microusd == 300_000
        assert usage.status == "UNCERTAIN"
        events = (await session.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "account_usage",
            AuditEvent.entity_id == str(usage_id),
        ))).all()
        assert len(events) == 1
        reserved = await reserve_usd(
            session, user_id=ids["member"], kind="IMAGE",
            operation_id=uuid.uuid4(), units=1,
        )
        assert reserved.amount_microusd == 100_000
        await settle_usd(session, kind="VIDEO", operation_id=usage.operation_id,
                         status="ESTIMATED", actual_amount_microusd=350_000)
        await session.flush()
        await session.refresh(usage)
        assert usage.amount_microusd == 350_000


async def test_confirmation_reserves_saved_duration_and_replay_once(tmp_path):
    from test_workflow_integration import (
        USER_ID,
        create_analyzed_task,
        running_app,
        user_headers,
    )

    async with running_app(tmp_path) as (client, app):
        app.state.settings.auto_run_fake_commands = False
        task = await create_analyzed_task(client, variants=["STABLE"])
        async with app.state.database.session_factory() as session:
            user = await session.get(User, uuid.UUID(USER_ID))
            user.daily_budget_microusd = 3_000_000
            user.video_price_microusd = 250_000
            variant = await session.scalar(select(GenerationVariant))
            variant.generation_config_json = {**variant.generation_config_json, "duration": 12}
            await session.commit()
        url = f"/api/v1/tasks/{task['id']}/confirm"
        headers = user_headers("duration-budget-confirm")
        payload = {"expectedStateVersion": task["stateVersion"], "acceptRisk": True}
        response = await client.post(url, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        replay = await client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        async with app.state.database.session_factory() as session:
            records = list(await session.scalars(select(AccountUsage)))
            assert len(records) == 1
            assert records[0].units == 12
            assert records[0].amount_microusd == 3_000_000
            with pytest.raises(HTTPException) as error:
                await reserve_usd(
                    session, user_id=uuid.UUID(USER_ID), kind="VIDEO",
                    operation_id=uuid.uuid4(), units=3,
                )
            assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"


async def test_unpriced_and_zero_budget_fail_closed_and_day_rollover(account_app):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        user = await session.get(User, ids["member"])
        user.daily_budget_microusd = 0
        await session.flush()
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=user.id, kind="IMAGE", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.detail["code"] == "USD_PRICE_REQUIRED"
        user.image_price_microusd = 1
        await session.flush()
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                session, user_id=user.id, kind="IMAGE", operation_id=uuid.uuid4(), units=1
            )
        assert error.value.status_code == 429
        user.daily_budget_microusd = 1
        await session.flush()
        for instant in (
            datetime(2026, 10, 15, 15, 59, tzinfo=UTC),
            datetime(2026, 10, 15, 16, 0, tzinfo=UTC),
        ):
            await reserve_usd(
                session,
                user_id=user.id,
                kind="IMAGE",
                operation_id=uuid.uuid4(),
                units=1,
                now=instant,
            )
        assert await session.scalar(select(func.count()).select_from(AccountUsage)) == 2


async def test_catalog_overrides_are_brand_local(account_app):
    app, _, ids = account_app
    async with app.state.database.session_factory() as session:
        base = next(iter((await motion_action_library(session)).values())).to_public_dict()
        for name, label in (("member", "品牌 A 私有动作"), ("other", "品牌 B 私有动作")):
            await save_catalog_entry(
                session,
                entry_type=MOTION_ACTION,
                entry_id=base["id"],
                payload={**base, "label": label},
                actor_id=ids[name],
            )
        await session.commit()
        session.info["brand_id"] = ids["a"]
        assert (await motion_action_library(session))[base["id"]].label == "品牌 A 私有动作"
        session.info["brand_id"] = ids["b"]
        assert (await motion_action_library(session))[base["id"]].label == "品牌 B 私有动作"
        session.info["brand_id"] = None
        assert (await motion_action_library(session))[base["id"]].label == base["label"]


def test_classic_migration_preserves_existing_identity_and_is_idempotent(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "classic_access_migrate", root / "scripts/classic_access_migrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "classic.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, username TEXT, "
            "password_hash TEXT, role TEXT)"
        )
        connection.execute(
            "INSERT INTO app_user VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex, "existing", "keep-current-hash", "ADMIN"),
        )
        connection.execute(
            "CREATE TABLE image_generation_batch (id CHAR(32) PRIMARY KEY, "
            "user_id CHAR(32), created_at DATETIME, status TEXT, "
            "expected_generation_count INTEGER)"
        )
        connection.execute(
            "INSERT INTO image_generation_batch SELECT ?, id, ?, 'COMPLETED', 3 FROM app_user",
            (uuid.uuid4().hex, "2026-10-15 16:01:00"),
        )
    assert module.upgrade_database(path)["status"] == "pending"
    result = module.upgrade_database(path, apply=True)
    assert Path(result["backup"]).is_file()
    assert module.upgrade_database(path, apply=True)["status"] == "already_current"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT password_hash, brand_id, is_brand_admin FROM app_user"
        ).fetchone() == ("keep-current-hash", None, 0)
        assert connection.execute(
            "SELECT kind, business_date, units, amount_microusd, status FROM account_usage"
        ).fetchone() == ("IMAGE", "2026-10-16", 3, None, "ESTIMATED")
