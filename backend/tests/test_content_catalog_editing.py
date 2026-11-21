from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import Base
from libtv_agent.services.content_catalog import (
    MOTION_ACTION,
    SITE_STYLE_PROFILE,
    next_patch_version,
    save_catalog_entry,
)
from libtv_agent.templates.motion_actions import ACTION_LIBRARY
from libtv_agent.templates.site_style_profiles import get_site_style_profile
from sqlalchemy.ext.asyncio import create_async_engine


def motion_action_payload() -> dict[str, object]:
    return {
        "id": "catalog_test_pause",
        "label": "商品定格展示",
        "category": "product",
        "instruction": "保持主体稳定站立，将商品完整朝向镜头，并自然停留两秒后回到放松姿态。",
        "purpose": "为商品主体提供清晰、稳定且可复用的展示片段。",
        "compatibleTemplates": ["garment_detail", "mood_model"],
        "requirements": ["商品在输入画面中完整可见"],
        "avoid": ["遮挡商品主体", "突然快速移动"],
        "riskLevel": "low",
    }


def restricted_active_profile_payload() -> dict[str, object]:
    return {
        "id": "restricted_catalog_test",
        "status": "active",
        "name": "受限证据测试风格",
        "siteUrl": "https://example.invalid/",
        "sourceUrls": ["https://example.invalid/"],
        "retrievedAt": "2026-07-30",
        "evidenceLevel": "restricted_site",
        "tone": "克制、自然、强调商品轮廓的日常表达。",
        "audience": "偏好简洁穿搭与清晰商品展示的用户。",
        "palette": ["black", "white"],
        "typography": "简洁无衬线字体",
        "cameraLanguage": "使用稳定中景与一次缓慢推进，始终保持商品主体清楚可见。",
        "openingRule": "以稳定中景建立人物、商品与画面空间的关系。",
        "middleRule": "通过一次低幅度姿态变化展示商品正面与局部细节。",
        "endingRule": "回到商品完整可见的稳定画面并自然停留。",
        "preferredShots": ["正面中景", "商品细节近景"],
        "preferredActions": ["stance_shift"],
        "pacing": "slow_to_standard",
        "productPriorities": ["商品轮廓", "材质细节"],
        "forbiddenMismatches": ["遮挡商品", "快速甩镜"],
        "researchNote": "该测试资料来源受限，因此不允许直接激活。",
    }


def error_message(response) -> str:
    detail = response.json()["detail"]
    return detail if isinstance(detail, str) else detail["message"]


@pytest.fixture
async def catalog_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Settings]]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    settings = Settings(
        app_env="test",
        database_url=database_url,
        object_store_mode="local",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, settings


@pytest.mark.asyncio
async def test_regular_user_cannot_edit_admin_only_content_catalog(
    catalog_client: tuple[AsyncClient, Settings],
) -> None:
    client, settings = catalog_client
    user_headers = {"X-User-Id": settings.dev_user_id}

    create_response = await client.post(
        "/api/v1/motion-actions",
        headers=user_headers,
        json=motion_action_payload(),
    )
    assert create_response.status_code == 403

    # The legacy read contract still permits make-page users to read style choices.
    profiles_response = await client.get(
        "/api/v1/site-style-profiles", headers=user_headers
    )
    assert profiles_response.status_code == 200
    profile = next(item for item in profiles_response.json() if item["id"] == "geeksoutfit")
    profile["tone"] = "should not be saved"

    edit_response = await client.put(
        "/api/v1/site-style-profiles/geeksoutfit",
        headers=user_headers,
        json=profile,
    )
    assert edit_response.status_code == 403


@pytest.mark.asyncio
async def test_admin_creates_motion_action_and_get_refresh_keeps_it(
    catalog_client: tuple[AsyncClient, Settings],
) -> None:
    client, settings = catalog_client
    admin_headers = {"X-User-Id": settings.dev_admin_id}
    payload = motion_action_payload()

    created = await client.post(
        "/api/v1/motion-actions",
        headers=admin_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert created.json()["id"] == payload["id"]
    assert created.json()["compatibleTemplates"] == payload["compatibleTemplates"]

    refreshed = await client.get("/api/v1/motion-actions", headers=admin_headers)
    assert refreshed.status_code == 200
    persisted = next(item for item in refreshed.json() if item["id"] == payload["id"])
    assert persisted["instruction"] == payload["instruction"]
    assert persisted["purpose"] == payload["purpose"]


@pytest.mark.asyncio
async def test_admin_edits_style_with_version_increment_and_cannot_activate_restricted_evidence(
    catalog_client: tuple[AsyncClient, Settings],
) -> None:
    client, settings = catalog_client
    admin_headers = {"X-User-Id": settings.dev_admin_id}

    initial = await client.get("/api/v1/site-style-profiles", headers=admin_headers)
    assert initial.status_code == 200
    profile = next(item for item in initial.json() if item["id"] == "geeksoutfit")
    original_version = profile.pop("version")
    updated_tone = "轻快、清晰、突出商品信息，并保留自然的日常穿搭氛围。"
    profile["tone"] = updated_tone

    updated = await client.put(
        "/api/v1/site-style-profiles/geeksoutfit",
        headers=admin_headers,
        json=profile,
    )
    assert updated.status_code == 200, updated.text
    expected_version = next_patch_version(original_version)
    assert updated.json()["version"] == expected_version
    assert updated.json()["tone"] == updated_tone

    refreshed = await client.get("/api/v1/site-style-profiles", headers=admin_headers)
    persisted = next(item for item in refreshed.json() if item["id"] == "geeksoutfit")
    assert persisted["version"] == expected_version
    assert persisted["tone"] == updated_tone

    restricted = await client.post(
        "/api/v1/site-style-profiles",
        headers=admin_headers,
        json=restricted_active_profile_payload(),
    )
    assert restricted.status_code == 422
    assert "verified site evidence" in error_message(restricted)


@pytest.mark.asyncio
async def test_existing_catalog_entries_are_assigned_to_the_expected_libraries(
    catalog_client: tuple[AsyncClient, Settings],
) -> None:
    client, settings = catalog_client
    admin_headers = {"X-User-Id": settings.dev_admin_id}

    actions = await client.get("/api/v1/motion-actions", headers=admin_headers)
    profiles = await client.get("/api/v1/site-style-profiles", headers=admin_headers)

    assert actions.status_code == 200
    assert profiles.status_code == 200
    assert {item["libraryScope"] for item in actions.json()} == {"general"}
    assert all(item["brandId"] is None for item in actions.json())
    assert all(item["baseActionId"] is None for item in actions.json())
    assert {item["libraryScope"] for item in profiles.json()} == {"brand"}
    assert all(item["brandId"] == item["id"] for item in profiles.json())


@pytest.mark.asyncio
async def test_brand_action_requires_a_brand_and_cannot_leak_into_another_brand_profile(
    catalog_client: tuple[AsyncClient, Settings],
) -> None:
    client, settings = catalog_client
    admin_headers = {"X-User-Id": settings.dev_admin_id}
    payload = motion_action_payload()
    payload["libraryScope"] = "brand"

    missing_brand = await client.post(
        "/api/v1/motion-actions",
        headers=admin_headers,
        json=payload,
    )
    assert missing_brand.status_code == 422
    assert "require a brand id" in error_message(missing_brand)

    payload["brandId"] = "geeksoutfit"
    missing_base = await client.post(
        "/api/v1/motion-actions",
        headers=admin_headers,
        json=payload,
    )
    assert missing_base.status_code == 422
    assert "general base action id" in error_message(missing_base)

    payload["baseActionId"] = "stance_shift"
    created = await client.post(
        "/api/v1/motion-actions",
        headers=admin_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert created.json()["libraryScope"] == "brand"
    assert created.json()["brandId"] == "geeksoutfit"
    assert created.json()["baseActionId"] == "stance_shift"

    profiles = await client.get("/api/v1/site-style-profiles", headers=admin_headers)
    guidingcross = next(item for item in profiles.json() if item["id"] == "guidingcross")
    guidingcross.pop("version")
    guidingcross["preferredActions"].append(payload["id"])

    cross_brand = await client.put(
        "/api/v1/site-style-profiles/guidingcross",
        headers=admin_headers,
        json=guidingcross,
    )
    assert cross_brand.status_code == 422
    assert "another brand" in error_message(cross_brand)


@pytest.mark.asyncio
async def test_upper_scope_filters_persisted_overrides_and_style_pools(catalog_client):
    client, settings = catalog_client
    headers = {"X-User-Id": settings.dev_admin_id}
    assert (await client.get("/api/v1/motion-actions", headers=headers)).status_code == 200
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            for id_ in ("footwear_forward_hold", "stance_shift"):
                payload = ACTION_LIBRARY[id_].to_public_dict()
                payload.pop("productFocus")  # Historical payload predates the new scope field.
                payload["requiredCapabilities"] = []
                await save_catalog_entry(
                    session,
                    entry_type=MOTION_ACTION,
                    entry_id=id_,
                    payload=payload,
                    actor_id=UUID(settings.dev_admin_id),
                )
            brand = ACTION_LIBRARY["footwear_forward_hold"].to_public_dict()
            brand.update(
                id="brand_shoe",
                libraryScope="brand",
                brandId="geeksoutfit",
                baseActionId="footwear_forward_hold",
                productFocus="upper_garment",
            )
            await save_catalog_entry(
                session,
                entry_type=MOTION_ACTION,
                entry_id=brand["id"],
                payload=brand,
                actor_id=UUID(settings.dev_admin_id),
            )
            profile = get_site_style_profile("geeksoutfit").to_public_dict()
            for key in ("preferredActions", "primaryMotionPool", "microMotionPool"):
                profile[key] = ["footwear_forward_hold", "brand_shoe", "stance_shift"]
            await save_catalog_entry(
                session,
                entry_type=SITE_STYLE_PROFILE,
                entry_id="geeksoutfit",
                payload=profile,
                actor_id=UUID(settings.dev_admin_id),
            )
            await session.commit()
    finally:
        await database.dispose()
    actions = (await client.get("/api/v1/motion-actions", headers=headers)).json()
    assert len(actions) == 41
    assert {action["productFocus"] for action in actions} == {"upper_garment"}
    assert "footwear_forward_hold" not in {action["id"] for action in actions}
    assert "brand_shoe" not in {action["id"] for action in actions}
    assert next(a for a in actions if a["id"] == "stance_shift")["requiredCapabilities"] == [
        "standing_pose",
    ]
    profiles = (await client.get("/api/v1/site-style-profiles", headers=headers)).json()
    profile = next(p for p in profiles if p["id"] == "geeksoutfit")
    for key in ("preferredActions", "primaryMotionPool", "microMotionPool"):
        assert profile[key] == ["stance_shift"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"productFocus": "lower_garment"},
        {"productFocus": "footwear"},
        {"requiredCapabilities": ["footwear_visible"]},
        {
            "libraryScope": "brand",
            "brandId": "geeksoutfit",
            "baseActionId": "footwear_forward_hold",
        },
        {"riskLevel": "conditional", "requiredCapabilities": []},
    ],
)
async def test_editor_rejects_non_upper_or_unconditional_unsafe_actions(catalog_client, changes):
    client, settings = catalog_client
    payload = {**motion_action_payload(), **changes}
    response = await client.post(
        "/api/v1/motion-actions", headers={"X-User-Id": settings.dev_admin_id}, json=payload
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_editor_preserves_focus_and_cannot_remove_safety_conditions(catalog_client):
    client, settings = catalog_client
    headers = {"X-User-Id": settings.dev_admin_id}
    actions = (await client.get("/api/v1/motion-actions", headers=headers)).json()
    action = next(item for item in actions if item["id"] == "controlled_walk")
    action.pop("productFocus")
    action["requiredCapabilities"] = ["standing_pose"]
    rejected = await client.put(
        "/api/v1/motion-actions/controlled_walk", headers=headers, json=action
    )
    assert rejected.status_code == 422
    assert "cannot be removed" in error_message(rejected)
    action.pop("requiredCapabilities")
    accepted = await client.put(
        "/api/v1/motion-actions/controlled_walk", headers=headers, json=action
    )
    assert accepted.status_code == 200
    assert accepted.json()["productFocus"] == "upper_garment"
    assert "walk_depth" in accepted.json()["requiredCapabilities"]
