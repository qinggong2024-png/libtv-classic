import asyncio
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.video_provider import ProviderAvailability, VideoGenerationRequest
from libtv_agent.adapters.wavespeed_video import WaveSpeedVideoAdapter
from libtv_agent.app import create_app
from libtv_agent.domain.models import AccountUsage, Base, GenerationCommand, User
from libtv_agent.runner.deep_video_pipeline import pipeline_request
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services.deep_video_media import DEPTH_MODEL, RECREATE_MODEL
from libtv_agent.services.tasks import get_task, task_to_read
from PIL import Image
from sqlalchemy import select
from test_deep_video import reference_video as reference_video
from test_deep_video import settings_for
from test_wavespeed_video_adapter import FakeWaveSpeedClient


@pytest.mark.parametrize("source", ["original", "depth"])
async def test_explicit_depth_bypasses_extraction_and_its_budget(
    tmp_path: Path,
    reference_video: Path,
    monkeypatch,
    source,
):
    async def available(self):
        return ProviderAvailability("AVAILABLE", True, True)

    async def allow(*args, **kwargs):
        pass

    monkeypatch.setattr(WaveSpeedVideoAdapter, "check_availability", available)
    monkeypatch.setattr(
        "libtv_agent.services.fixed_video_tasks.ensure_provider_submission_allowed", allow
    )
    settings = settings_for(tmp_path, wavespeed_enabled=True, wavespeed_api_key="synthetic-only")
    app = create_app(settings)
    video_bytes = await asyncio.to_thread(reference_video.read_bytes)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "video"}
            uploaded = await client.post(
                "/api/v1/uploads/video",
                headers=headers,
                files={"file": ("depth.mp4", video_bytes, "video/mp4")},
            )
            assert uploaded.status_code == 201, uploaded.text
            content = io.BytesIO()
            Image.new("RGB", (256, 384), "red").save(content, format="PNG")
            image = await client.post(
                "/api/v1/uploads",
                headers={**headers, "Idempotency-Key": "image"},
                files={"file": ("target.png", content.getvalue(), "image/png")},
            )
            assert image.status_code == 201, image.text
            async with app.state.database.session_factory() as session:
                user = await session.get(User, uuid.UUID(settings.dev_user_id))
                user.daily_budget_microusd = 10_000_000
                user.depth_video_price_microusd = 50_000
                user.recreate_video_price_microusd = 200_000
                await session.commit()
            payload = {
                "clientTaskId": "direct-depth",
                "title": "Saved depth replacement",
                "referenceVideoUploadId": uploaded.json()["id"],
                "referenceVideoType": source,
                "mode": "recreate",
                "subjectImages": [{"uploadId": image.json()["id"], "order": 1}],
                "prompt": "Replace with @Image 1",
                "paidSubmissionConsent": True,
            }
            foreign = await client.post(
                "/api/v1/video-tasks/deep",
                json=payload,
                headers={"X-User-Id": settings.dev_admin_id, "Idempotency-Key": "foreign"},
            )
            assert foreign.status_code == 422
            create_headers = {**headers, "Idempotency-Key": "create"}
            response = await client.post(
                "/api/v1/video-tasks/deep", headers=create_headers, json=payload
            )
            assert response.status_code == 201, response.text
            task = response.json()
            assert task["phase"] == "QUEUED"
            assert task["settings"]["deepVideoPipeline"] == (
                "depth-input-v1" if source == "depth" else "depth-first-v1"
            )
            assert (
                await client.post("/api/v1/video-tasks/deep", headers=create_headers, json=payload)
            ).json() == task
        async with app.state.database.session_factory() as session:
            usage = await session.scalar(select(AccountUsage))
            assert usage.pricing_category == (
                "RECREATE_VIDEO" if source == "depth" else "RECREATE_DEPTH_VIDEO"
            )
            assert usage.amount_microusd == (1_200_000 if source == "depth" else 1_500_000)
            fake = FakeWaveSpeedClient()
            supervisor = RunnerSupervisor(
                app.state.libtv_adapter,
                app.state.object_store,
                settings,
                kling_adapter=WaveSpeedVideoAdapter(settings, client=fake),
            )
            assert await supervisor.run_ready(session) == 1
            assert len(fake.submissions) == 1
            model, submitted, _ = fake.submissions[0]
            assert model == (RECREATE_MODEL if source == "depth" else DEPTH_MODEL)
            assert await asyncio.to_thread(fake.uploads[0].read_bytes) == video_bytes
            if source == "depth":
                assert submitted["reference_images"]
                command = await session.scalar(select(GenerationCommand))
                assert command.command_manifest_json["depthPipeline"]["depthInput"] == "uploaded"
                assert command.command_manifest_json["depthPipeline"]["replacementTaskId"]
                stored = await get_task(
                    session, uuid.UUID(settings.dev_user_id), uuid.UUID(task["id"])
                )
                read = await task_to_read(session, stored, settings.daily_user_points_limit)
                assert read.depth_video_result.download_id == command.id
                assert "REPLACEMENT" in [stage.key for stage in read.timing.stages]
                assert "DEPTH_EXTRACTION" not in [stage.key for stage in read.timing.stages]


async def test_invalid_saved_depth_stops_before_paid_submission(tmp_path):
    settings = settings_for(tmp_path)
    variant = SimpleNamespace(
        generation_config_json={"deepVideoPipeline": "depth-input-v1", "sourceDurationSeconds": 5}
    )
    request = VideoGenerationRequest(
        prompt="Replace", model=RECREATE_MODEL, reference_video_path=tmp_path / "missing.mp4"
    )
    with pytest.raises(KlingCliError) as error:
        await pipeline_request(None, None, None, None, variant, request, None, None, settings)
    assert not error.value.submitted
