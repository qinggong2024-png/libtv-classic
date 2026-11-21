"""Storefront identity, ownership and historical binding on isolated databases."""

import asyncio
import copy
from dataclasses import replace

import pytest
import test_cutflow_handoff as shared
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import AuditEvent, Brand, CutflowHandoff, ParentTask, User
from libtv_agent.services.cutflow_handoff import CutflowHandoffError
from libtv_agent.services.cutflow_site import (
    CutflowSiteError,
    bind_missing_task_site,
    creation_snapshot,
    site_choices,
)
from sqlalchemy import func, select

handoff_env = shared.handoff_env
PAGEWINGS_ID = "158b00a8-6166-5623-b977-e32003b3dc68"


async def clear_site(env):
    database, _settings, _store, ids = env
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.site_profile_id = task.site_profile_version = None
        await session.commit()


async def bind(env, profile="pagewings", user=None):
    database, _settings, _store, ids = env
    async with database.session_factory() as session:
        result = await bind_missing_task_site(session, user or ids["user"], ids["task"], profile)
        await session.commit()
        return result


async def test_unbound_account_has_no_guessed_default(handoff_env):
    async with handoff_env[0].session_factory() as session:
        choices, default = await site_choices(session, handoff_env[3]["user"])
        assert len(choices) > 1 and default is None
        assert await creation_snapshot(session, handoff_env[3]["user"], None) is None
        snapshot = await creation_snapshot(session, handoff_env[3]["user"], "pagewings")
        assert snapshot["siteId"] == PAGEWINGS_ID
        assert snapshot["selection"] == "explicit"


async def test_explicit_account_brand_defaults_only_to_exact_brand(handoff_env):
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        brand = Brand(name="泛读书")
        session.add(brand)
        await session.flush()
        actor = await session.get(User, ids["user"])
        actor.brand_id = brand.id
        await session.commit()
        choices, default = await site_choices(session, ids["user"])
        assert [item.id for item in choices] == ["pagewings"]
        assert default == "pagewings"
        snapshot = await creation_snapshot(session, ids["user"], None)
        assert snapshot["selection"] == "account_brand"
        with pytest.raises(CutflowSiteError):
            await creation_snapshot(session, ids["user"], "guidingcross")
        brand.name = "读书"
        await session.flush()
        assert await site_choices(session, ids["user"]) == ([], None)


async def test_historical_binding_is_once_and_preserves_generation(handoff_env):
    await clear_site(handoff_env)
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.settings_json = {"generationConfig": {"prompt": "leave this unchanged"}}
        task.compiled_plan_json = {"promptHash": "original-hash"}
        await session.commit()
        before = (copy.deepcopy(task.settings_json), copy.deepcopy(task.compiled_plan_json))
    snapshot = await bind(handoff_env)
    assert snapshot == await bind(handoff_env)
    with pytest.raises(CutflowSiteError):
        await bind(handoff_env, "guidingcross")
    with pytest.raises(CutflowSiteError):
        await bind(handoff_env, user=ids["other"])
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        assert task.site_profile_id == "pagewings"
        assert task.settings_json["generationConfig"] == before[0]["generationConfig"]
        assert task.compiled_plan_json == before[1]
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1


async def test_parallel_bind_and_enqueue_keep_single_frozen_identity(handoff_env):
    await clear_site(handoff_env)
    await asyncio.gather(*(bind(handoff_env) for _ in range(5)))
    rows = await asyncio.gather(*(shared.enqueue(handoff_env) for _ in range(5)))
    assert len({row.id for row in rows}) == 1
    assert rows[0].manifest_json["source"]["site_id"] == PAGEWINGS_ID
    original = copy.deepcopy(rows[0].manifest_json)
    await bind(handoff_env)
    with pytest.raises(CutflowSiteError):
        await bind(handoff_env, "guidingcross")
    assert (await shared.enqueue(handoff_env)).manifest_json == original


@pytest.mark.parametrize("change", ["userId", "siteId", "siteUrl", "profileVersion", "malformed"])
async def test_tampered_snapshot_cannot_publish(handoff_env, change):
    await clear_site(handoff_env)
    await bind(handoff_env)
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        snapshot = dict(task.settings_json["cutflowSite"])
        snapshot[change] = "invalid"
        task.settings_json = {"cutflowSite": "broken" if change == "malformed" else snapshot}
        await session.commit()
    with pytest.raises(CutflowHandoffError):
        await shared.enqueue(handoff_env)
    assert not handoff_env[1].cutflow_handoff_root.exists()


async def test_catalog_patch_keeps_identity_but_changed_domain_rejects(handoff_env, monkeypatch):
    await clear_site(handoff_env)
    snapshot = await bind(handoff_env)
    import libtv_agent.services.cutflow_handoff as handoff_service

    original = handoff_service.get_merged_site_style_profile

    async def patched(*args, **kwargs):
        return replace(await original(*args, **kwargs), version="1.0.1")

    monkeypatch.setattr(handoff_service, "get_merged_site_style_profile", patched)
    row = await shared.enqueue(handoff_env)
    assert row.manifest_json["source"]["site_profile_version"] == snapshot["profileVersion"]
    async with handoff_env[0].session_factory() as session:
        await session.delete(await session.get(CutflowHandoff, row.id))
        await session.commit()

    async def wrong_domain(*args, **kwargs):
        return replace(await original(*args, **kwargs), site_url="https://different.example/")

    monkeypatch.setattr(handoff_service, "get_merged_site_style_profile", wrong_domain)
    with pytest.raises(CutflowHandoffError):
        await shared.enqueue(handoff_env)


@pytest.mark.parametrize("invalid", ["unknown", "generic"])
async def test_unavailable_profile_not_saved(handoff_env, invalid):
    await clear_site(handoff_env)
    with pytest.raises(CutflowSiteError):
        await bind(handoff_env, invalid)


async def test_site_api_owner_and_readonly_permissions(handoff_env):
    database, settings, _store, ids = handoff_env
    await clear_site(handoff_env)
    app = create_app(settings)
    app.state.database = database
    headers = {"X-User-Id": str(ids["user"])}
    body = {"taskId": str(ids["task"]), "siteProfileId": "pagewings"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        choices = await client.get("/api/v1/cutflow-handoffs/sites", headers=headers)
        assert choices.status_code == 200
        assert (await client.post("/api/v1/cutflow-handoffs/source-site", json=body,
                                  headers={"X-User-Id": str(ids["other"])})).status_code == 409
        async with database.session_factory() as session:
            actor = await session.get(User, ids["user"])
            actor.role = UserRole.AUDITOR
            await session.commit()
        assert (await client.post("/api/v1/cutflow-handoffs/source-site", json=body,
                                  headers=headers)).status_code == 403
        async with database.session_factory() as session:
            actor = await session.get(User, ids["user"])
            actor.role = UserRole.USER
            actor.page_permissions_json = ["make"]
            await session.commit()
        assert (await client.post("/api/v1/cutflow-handoffs/source-site", json=body,
                                  headers=headers)).status_code == 200
