import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from libtv_agent.domain.models import Asset, User
from test_workflow_integration import (
    OTHER_USER_ID,
    USER_ID,
    create_draft_task,
    running_app,
    user_headers,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("video", [False, True])
async def test_asset_preview_content_and_owner_isolation(tmp_path: Path, video: bool) -> None:
    async with running_app(tmp_path) as (client, app):
        task = await create_draft_task(client, "preview")
        asset_id = task["assets"][0]["id"]
        path = f"/api/v1/assets/{asset_id}/content"
        async with app.state.database.session_factory() as session:
            asset = await session.get(Asset, uuid.UUID(asset_id))
            if video:
                media = tmp_path / "reference.mp4"
                media.write_bytes(b"synthetic-video-bytes-for-range-check")
                asset.object_key = await app.state.object_store.put_file(
                    media, "preview/reference.mp4", "video/mp4"
                )
                asset.mime_type = "video/mp4"
                asset.role = "REFERENCE_VIDEO"
                await session.commit()
            object_key = asset.object_key

        response = await client.get(path, headers=user_headers())
        assert response.status_code == 200
        assert response.headers["content-type"] == ("video/mp4" if video else "image/jpeg")
        assert "no-store" in response.headers["cache-control"]
        content = response.content
        assert content
        partial = await client.get(path, headers={**user_headers(), "Range": "bytes=0-9"})
        assert partial.status_code == 206
        assert partial.content == content[:10]
        assert (await client.get(path)).status_code == 401
        other = await client.get(path, headers=user_headers(user_id=OTHER_USER_ID))
        assert other.status_code == 404
        missing = await client.get(f"/api/v1/assets/{uuid.uuid4()}/content", headers=user_headers())
        assert missing.status_code == 404

        await app.state.object_store.delete(object_key)
        assert (await client.get(path, headers=user_headers())).status_code == 404


@pytest.mark.asyncio
async def test_deleted_asset_cannot_be_previewed(tmp_path: Path) -> None:
    async with running_app(tmp_path) as (client, app):
        task = await create_draft_task(client, "deleted-preview")
        asset_id = task["assets"][0]["id"]
        async with app.state.database.session_factory() as session:
            asset = await session.get(Asset, uuid.UUID(asset_id))
            asset.deleted_at = datetime.now(UTC)
            await session.commit()
        response = await client.get(f"/api/v1/assets/{asset_id}/content", headers=user_headers())
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_asset_preview_page_grant_and_unsupported_media(tmp_path: Path) -> None:
    async with running_app(tmp_path) as (client, app):
        task = await create_draft_task(client, "preview-grants")
        asset_id = task["assets"][0]["id"]
        path = f"/api/v1/assets/{asset_id}/content"
        async with app.state.database.session_factory() as session:
            user = await session.get(User, uuid.UUID(USER_ID))
            user.page_permissions_json = ["images"]
            await session.commit()
        assert (await client.get(path, headers=user_headers())).status_code == 403
        async with app.state.database.session_factory() as session:
            user = await session.get(User, uuid.UUID(USER_ID))
            user.page_permissions_json = ["records"]
            await session.commit()
        assert (await client.get(path, headers=user_headers())).status_code == 200
        async with app.state.database.session_factory() as session:
            asset = await session.get(Asset, uuid.UUID(asset_id))
            asset.mime_type = "text/html"
            await session.commit()
        assert (await client.get(path, headers=user_headers())).status_code == 415
