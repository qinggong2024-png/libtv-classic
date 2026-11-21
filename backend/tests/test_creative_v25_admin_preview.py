"""V2.5 admin read-only preview admission and no-Kling contract."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.api import creative_v25_preview as api
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase, UserRole
from libtv_agent.domain.models import Asset, Base, ParentTask, User
from libtv_agent.services.ai_creative_planner import CompiledCreativeVariant
from sqlalchemy import select


@pytest.fixture
async def preview_app(tmp_path, monkeypatch):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'preview.db'}",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        object_store_mode="local",
        ai_adapter_mode="openai",
        openai_api_key="test-not-real",
        _env_file=None,
    )
    app = create_app(settings)
    task_id = uuid.uuid4()
    root_id = uuid.uuid4()
    member_id = uuid.uuid4()
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with app.state.database.session_factory() as session:
            session.add_all([
                User(
                    id=root_id, username="classic-admin", password_hash="unused",
                    role=UserRole.ADMIN, is_active=True,
                ),
                User(
                    id=member_id, username="ordinary-user", password_hash="unused",
                    role=UserRole.USER, is_active=True,
                ),
                ParentTask(
                    id=task_id, user_id=member_id,
                    client_task_id="admin-v25-fixture", title="Pagewings reader",
                    asset_count=2, phase=ParentTaskPhase.DRAFT,
                    workflow_kind="FIXED_TEMPLATE",
                    site_profile_id="pagewings",
                    settings_json={
                        "durationSeconds": 10, "resultCount": 1,
                        "cutMode": "explicit_cuts",
                    },
                    user_requirement_raw="Reader walks through a bookstore",
                ),
            ])
            await session.flush()
            session.add_all([
                Asset(
                    parent_task_id=task_id, role=role, order_no=order,
                    original_filename=filename, sha256="a" * 64,
                    mime_type="image/jpeg", size_bytes=1024,
                    width=800, height=900, object_key=key,
                )
                for role, order, filename, key in [
                    ("STORYBOARD", 1, "frame.jpg", "fixtures/frame.jpg"),
                    ("PRODUCT", 101, "sweater.jpg", "fixtures/sweater.jpg"),
                ]
            ])
            await session.commit()
        image_calls = []

        async def fake_image(store, object_key, asset_id, root):
            image_calls.append(object_key)
            return "data:image/jpeg;base64,YWE="
        monkeypatch.setattr(api, "_analysis_image_url", fake_image)
        calls = []

        async def fake_planner(**kwargs):
            calls.append(kwargs)
            return tuple(
                CompiledCreativeVariant(
                    prompt="Test prompt — @image_1 scene and @image_2 garment",
                    creative_idea=f"Visual idea {i}",
                    visual_hook="Visible product hook",
                    buyer_reason="Supported clothing buying reason",
                    beats=({"frame_order": 1, "action": "natural turn"},),
                    facts=({"frame_order": 1, "person_count": 1},),
                    planner_version="v2.5-fixture",
                    request_attempts=1,
                )
                for i in range(kwargs["variant_count"])
            )
        monkeypatch.setattr(api, "plan_creative_video", fake_planner)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield app, client, task_id, root_id, member_id, calls, image_calls


def body(task_id, **updates):
    return {
        "taskId": str(task_id), "model": "gpt-6.1-sol",
        "variantCount": 2, "confirmModelCost": True, **updates,
    }


@pytest.mark.asyncio
async def test_v25_admin_only_preview_is_read_only_and_never_queues_kling(preview_app):
    app, client, task_id, root, member, calls, image_calls = preview_app
    endpoint = "/api/v1/admin/creative-v25-preview"
    forbidden = await client.post(
        endpoint, headers={"X-User-Id": str(member)}, json=body(task_id),
    )
    assert forbidden.status_code == 403
    unconfirmed = await client.post(
        endpoint, headers={"X-User-Id": str(root)},
        json=body(task_id, confirmModelCost=False),
    )
    assert unconfirmed.status_code == 422
    assert not calls
    response = await client.post(
        endpoint, headers={"X-User-Id": str(root)}, json=body(task_id),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["klingSubmitted"] is False
    assert data["variantCount"] == 2
    assert len(data["variants"]) == 2
    assert len(calls) == 1
    assert calls[0]["experimental_v25"] is True
    assert calls[0]["settings"].openai_reasoning_effort == "low"
    assert [i.role for i in calls[0]["storyboard_images"]] == ["STORYBOARD"]
    assert [i.role for i in calls[0]["product_images"]] == ["PRODUCT"]
    assert image_calls == ["fixtures/frame.jpg", "fixtures/sweater.jpg"]
    async with app.state.database.session_factory() as session:
        saved = await session.get(ParentTask, task_id)
        assert saved.phase == ParentTaskPhase.DRAFT
        assert saved.compiled_plan_json is None
        assert saved.settings_json["resultCount"] == 1
        assert not list(await session.scalars(select(ParentTask.id).where(
            ParentTask.id != task_id,
        )))


@pytest.mark.asyncio
async def test_v25_preview_rejects_concurrent_paid_inference(preview_app):
    _, client, task_id, root, _, calls, _ = preview_app
    await api._preview_lock.acquire()
    try:
        response = await client.post(
            "/api/v1/admin/creative-v25-preview",
            headers={"X-User-Id": str(root)}, json=body(task_id),
        )
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "PREVIEW_BUSY"
        assert not calls
    finally:
        api._preview_lock.release()


@pytest.mark.asyncio
async def test_v25_preview_rejects_unsupported_duration_before_model_call(preview_app):
    app, client, task_id, root, _, calls, _ = preview_app
    async with app.state.database.session_factory() as session:
        task = await session.get(ParentTask, task_id)
        task.settings_json = {**task.settings_json, "durationSeconds": 16}
        await session.commit()
    response = await client.post(
        "/api/v1/admin/creative-v25-preview",
        headers={"X-User-Id": str(root)}, json=body(task_id),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PREVIEW_DURATION"
    assert not calls


@pytest.mark.asyncio
async def test_v25_preview_rejects_four_plus_four_before_any_model_call(preview_app, monkeypatch):
    """4+4 is valid for planner numbering, but exceeds production's seven references."""
    _, client, task_id, root, _, calls, _ = preview_app

    async def too_many_refs(_session, _task):
        return [object() for _ in range(4)], [object() for _ in range(4)], None

    monkeypatch.setattr(api, "_existing_reference_inputs", too_many_refs)
    response = await client.post(
        "/api/v1/admin/creative-v25-preview",
        headers={"X-User-Id": str(root)}, json=body(task_id),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PREVIEW_REFERENCE_LIMIT"
    assert not calls
