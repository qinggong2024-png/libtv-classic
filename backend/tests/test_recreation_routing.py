"""Routing configuration never creates paid predictions."""

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.models import Base
from libtv_agent.services.deep_video_media import RECREATE_MODEL
from libtv_agent.services.wavespeed_config import load_wavespeed_admin_config
from test_deep_video import settings_for


@pytest.mark.asyncio
async def test_recreation_model_is_independent_persistent_and_admin_only(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    model = "synthetic/custom/video-edit"
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = {"X-User-Id": settings.dev_admin_id}
            initial = await client.get("/api/v1/admin/wavespeed/config", headers=admin)
            assert initial.json()["recreateVideoModel"] == RECREATE_MODEL
            denied = await client.patch(
                "/api/v1/admin/wavespeed/config",
                headers={"X-User-Id": settings.dev_user_id},
                json={"recreateVideoModel": model},
            )
            assert denied.status_code == 403
            saved = await client.patch(
                "/api/v1/admin/wavespeed/config", headers=admin,
                json={"recreateVideoModel": model},
            )
            assert saved.status_code == 200
            assert saved.json()["recreateVideoModel"] == model
            assert saved.json()["videoModel"] == initial.json()["videoModel"]
            assert saved.json()["enabled"] is False
            # A stale general WaveSpeed form must preserve the independent model field.
            general = await client.patch(
                "/api/v1/admin/wavespeed/config", headers=admin,
                json={"videoDuration": 8},
            )
            assert general.json()["recreateVideoModel"] == model
            fresh = settings_for(tmp_path)
            assert load_wavespeed_admin_config(fresh)
            assert fresh.wavespeed_recreate_video_model == model
            for invalid in ("", "../escape/video-edit", "https://example.com/video-edit",
                            "provider/reference-to-video", "provider/video-edit?bad=1"):
                response = await client.patch(
                    "/api/v1/admin/wavespeed/config", headers=admin,
                    json={"recreateVideoModel": invalid},
                )
                assert response.status_code == 422
            current = await client.get("/api/v1/admin/wavespeed/config", headers=admin)
            assert current.json()["recreateVideoModel"] == model
