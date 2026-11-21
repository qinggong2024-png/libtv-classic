"""Deep-video contracts with local media and synthetic providers only."""

import asyncio
import io
import math
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.video_provider import ProviderAvailability, VideoGenerationRequest
from libtv_agent.adapters.wavespeed import WaveSpeedPrediction
from libtv_agent.adapters.wavespeed_video import WaveSpeedVideoAdapter, WaveSpeedVideoError
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import (
    AccountUsage,
    Base,
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    StagedUpload,
    User,
)
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services.deep_video_media import (
    DEPTH_MODEL,
    FLASH_RECREATE_MODEL,
    LEGACY_RECREATE_MODEL,
    RECREATE_MODEL,
    VideoMetadata,
    inspect_reference_video,
)
from PIL import Image
from sqlalchemy import select
from test_kling_migrated_submission import install_submission_migration
from test_wavespeed_video_adapter import FakeWaveSpeedClient


@pytest.fixture
def reference_video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required")
    path = tmp_path / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x96:r=10",
            "-t",
            "5.2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def settings_for(tmp_path, **overrides):
    return Settings(
        **{
            "app_env": "test",
            "database_url": "sqlite+aiosqlite:///:memory:",
            "object_store_mode": "local",
            "artifact_root": tmp_path / "artifacts",
            "runner_root": tmp_path / "runner",
            "wavespeed_admin_config_file": tmp_path / "wavespeed-config.json",
            "_env_file": None,
            **overrides,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["depth", "recreate"])
async def test_deep_submission_payload(reference_video, tmp_path, mode):
    image = tmp_path / "subject.jpg"
    image.write_bytes(b"synthetic-image")
    client = FakeWaveSpeedClient()
    adapter = WaveSpeedVideoAdapter(settings_for(tmp_path), client=client)
    model = DEPTH_MODEL if mode == "depth" else RECREATE_MODEL
    request = VideoGenerationRequest(
        prompt="Replace subject",
        model=model,
        reference_video_path=reference_video,
        product_reference_paths=(image,) if mode == "recreate" else (),
        duration=6,
        aspect_ratio="9:16",
    )
    result = await adapter.submit(request)
    expected = {"video": "https://cdn.example/reference.mp4"}
    if mode == "recreate":
        expected.update(
            prompt="Replace subject",
            reference_images=["https://cdn.example/subject.jpg"],
            duration=6,
            aspect_ratio="9:16",
            resolution="720p",
            generate_audio=False,
            enable_web_search=False,
        )
    assert client.submissions == [(model, expected, False)]
    assert result.provider_model == model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", [DEPTH_MODEL, RECREATE_MODEL, FLASH_RECREATE_MODEL, "synthetic/custom/video-edit"]
)
async def test_fake_mode_does_not_authorize_real_deep_submission(reference_video, tmp_path, model):
    client = FakeWaveSpeedClient()
    adapter = WaveSpeedVideoAdapter(settings_for(tmp_path, app_env="development"), client=client)
    image = tmp_path / "subject.jpg"
    image.write_bytes(b"synthetic-image")
    with pytest.raises(WaveSpeedVideoError, match="尚未授权"):
        await adapter.submit(
            VideoGenerationRequest(
                prompt="Depth",
                model=model,
                reference_video_path=reference_video,
                product_reference_paths=(image,) if model != DEPTH_MODEL else (),
                duration=6,
                aspect_ratio="9:16",
            )
        )
    assert not client.uploads and not client.submissions


async def test_flash_edit_framing_failure_stops_before_upload(
    reference_video, tmp_path, monkeypatch,
):
    async def invalid_media(source, destination, aspect_ratio):
        raise ValueError("synthetic media failure")

    monkeypatch.setattr(
        "libtv_agent.adapters.wavespeed_video.prepare_flash_edit_input", invalid_media,
    )
    image = tmp_path / "subject.jpg"
    image.write_bytes(b"synthetic-image")
    client = FakeWaveSpeedClient()
    adapter = WaveSpeedVideoAdapter(settings_for(tmp_path), client=client)
    request = VideoGenerationRequest(
        prompt="Use @Image 1", model=FLASH_RECREATE_MODEL, reference_video_path=reference_video,
        product_reference_paths=(image,), duration=6, aspect_ratio="9:16",
    )
    with pytest.raises(WaveSpeedVideoError, match="720p") as error:
        await adapter.submit(request)
    assert error.value.code == "WAVESPEED_INPUT_INVALID"
    assert not error.value.submitted
    assert not client.uploads and not client.submissions


@pytest.mark.asyncio
@pytest.mark.parametrize("duration, valid", [(4, True), (15, True), (3.99, False), (15.01, False)])
async def test_seedance_edit_duration_limits(tmp_path, monkeypatch, duration, valid):
    async def metadata(path):
        return VideoMetadata(duration, 720, 1280)

    monkeypatch.setattr("libtv_agent.adapters.wavespeed_video.inspect_reference_video", metadata)
    image = tmp_path / "subject.jpg"
    image.write_bytes(b"synthetic-image")
    client = FakeWaveSpeedClient()
    adapter = WaveSpeedVideoAdapter(settings_for(tmp_path), client=client)
    request = VideoGenerationRequest(
        prompt="Replace person with @Image 1", model=RECREATE_MODEL,
        reference_video_path=tmp_path / "clip.mp4", product_reference_paths=(image,),
        duration=math.ceil(duration), aspect_ratio="9:16",
    )
    if valid:
        assert await adapter.validate_request(request) == RECREATE_MODEL
    else:
        with pytest.raises(WaveSpeedVideoError, match="4–15"):
            await adapter.submit(request)
    assert not client.uploads and not client.submissions


@pytest.mark.asyncio
async def test_existing_vace_task_keeps_its_saved_model_and_payload(reference_video, tmp_path):
    image = tmp_path / "subject.jpg"
    image.write_bytes(b"synthetic-image")
    client = FakeWaveSpeedClient()
    adapter = WaveSpeedVideoAdapter(settings_for(tmp_path), client=client)
    await adapter.submit(VideoGenerationRequest(
        prompt="Historical task", model=LEGACY_RECREATE_MODEL,
        reference_video_path=reference_video, product_reference_paths=(image,),
        duration=6, aspect_ratio="16:9",
    ))
    model, payload, _ = client.submissions[0]
    assert model == LEGACY_RECREATE_MODEL
    assert payload["task"] == "depth"
    assert payload["size"] == "1280*720"
    assert "reference_images" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("source_profile", [None, "pagewings"])
@pytest.mark.parametrize(("mode", "configured_model"), [
    ("depth", RECREATE_MODEL),
    ("recreate", RECREATE_MODEL),
    ("recreate", "synthetic/compatible/video-edit"),
    ("recreate", FLASH_RECREATE_MODEL),
])
async def test_upload_queue_replay_and_recovery(
    reference_video, tmp_path, monkeypatch, mode, configured_model, source_profile,
):
    async def available(self):
        return ProviderAvailability("AVAILABLE", True, True)

    async def allow(*args, **kwargs):
        pass

    monkeypatch.setattr(WaveSpeedVideoAdapter, "check_availability", available)
    monkeypatch.setattr(
        "libtv_agent.services.fixed_video_tasks.ensure_provider_submission_allowed",
        allow,
    )
    settings = settings_for(tmp_path, wavespeed_enabled=True, wavespeed_api_key="synthetic-test")
    app = create_app(settings)
    flash_edit = configured_model == FLASH_RECREATE_MODEL
    aspect_ratio = "16:9" if flash_edit and source_profile else "9:16"
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        if flash_edit:
            await install_submission_migration(app.state.database.session_factory)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Configure through the admin API, then let task creation reload the saved routing.
            admin_headers = {"X-User-Id": settings.dev_admin_id}
            configured = await client.patch(
                "/api/v1/admin/wavespeed/config", headers=admin_headers,
                json={"recreateVideoModel": configured_model},
            )
            assert configured.status_code == 200, configured.text
            status = await client.get(
                "/api/v1/video-tasks/deep/status", headers=admin_headers,
            )
            assert status.json()["recreateModel"] == configured_model
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "video-upload"}
            video_bytes = reference_video.read_bytes()
            uploaded = await client.post(
                "/api/v1/uploads/video",
                headers=headers,
                files={"file": ("clip.mp4", video_bytes, "video/mp4")},
            )
            assert uploaded.status_code == 201, uploaded.text
            replay = await client.post(
                "/api/v1/uploads/video",
                headers=headers,
                files={"file": ("clip.mp4", video_bytes, "video/mp4")},
            )
            assert replay.json() == uploaded.json()
            assert uploaded.json()["durationSeconds"] == pytest.approx(5.2)
            payload = {
                "clientTaskId": "deep-client",
                "title": "Deep video",
                "mode": mode,
                "referenceVideoUploadId": uploaded.json()["id"],
                "subjectImages": [],
                "prompt": "Use target identity and outfit",
                "aspectRatio": aspect_ratio,
                "paidSubmissionConsent": True,
                "siteProfileId": source_profile,
            }
            if mode == "recreate":
                content = io.BytesIO()
                Image.new("RGB", (256, 384), "red").save(content, format="PNG")
                image = await client.post(
                    "/api/v1/uploads",
                    headers={**headers, "Idempotency-Key": "image-upload"},
                    files={"file": ("subject.png", content.getvalue(), "image/png")},
                )
                assert image.status_code == 201, image.text
                content = io.BytesIO()
                Image.new("RGB", (256, 384), "green").save(content, format="PNG")
                second_image = await client.post(
                    "/api/v1/uploads",
                    headers={**headers, "Idempotency-Key": "second-image-upload"},
                    files={"file": ("second.png", content.getvalue(), "image/png")},
                )
                assert second_image.status_code == 201, second_image.text
                # Wire order is deliberately reversed: explicit order defines @Image N.
                payload["subjectImages"] = [
                    {"uploadId": second_image.json()["id"], "order": 2},
                    {"uploadId": image.json()["id"], "order": 1},
                ]
                background_content = io.BytesIO()
                Image.new("RGB", (512, 512), "blue").save(background_content, format="PNG")
                background_image = await client.post(
                    "/api/v1/uploads",
                    headers={**headers, "Idempotency-Key": "background-image-upload"},
                    files={"file": ("background.png", background_content.getvalue(), "image/png")},
                )
                assert background_image.status_code == 201, background_image.text
                payload["backgroundImageUploadId"] = background_image.json()["id"]
                payload["prompt"] = "Use @Image 2 for the product and @Image 1 for the person"
                for invalid in ["Use @Image 3", "Use @Image 0", "[参考图已移除，请重新选择]"]:
                    rejected = await client.post(
                        "/api/v1/video-tasks/deep",
                        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                        json={**payload, "prompt": invalid},
                    )
                    assert rejected.status_code == 422, rejected.text
                    assert rejected.json()["detail"]["code"] == "IMAGE_REFERENCE_INVALID"
                async with app.state.database.session_factory() as session:
                    assert not list(await session.scalars(select(GenerationCommand)))
                    assert all(
                        item.claimed_task_id is None
                        for item in await session.scalars(select(StagedUpload))
                    )
            async with app.state.database.session_factory() as session:
                user = await session.get(User, uuid.UUID(settings.dev_user_id))
                user.daily_budget_microusd = 5_000_000
                user.video_price_microusd = 900_000
                user.depth_video_price_microusd = 50_000
                user.recreate_video_price_microusd = 200_000
                await session.commit()
            create_headers = {**headers, "Idempotency-Key": "create-deep"}
            result = await client.post(
                "/api/v1/video-tasks/deep", headers=create_headers, json=payload
            )
            assert result.status_code == 201, result.text
            task = result.json()
            assert task["workflowKind"] == "DEEP_VIDEO"
            assert task["phase"] == "QUEUED"
            assert task["analysisRunId"] is None
            assert task["settings"]["durationSeconds"] == 6
            assert task["assets"][0]["role"] == "REFERENCE_VIDEO"
            if mode == "recreate":
                assert [asset["role"] for asset in task["assets"]] == [
                    "REFERENCE_VIDEO", "PRODUCT", "PRODUCT", "BACKGROUND_REFERENCE",
                ]
                assert (
                    "Do not reproduce the source video's background"
                    in task["variants"][0]["promptText"]
                )
                assert (
                    "@Image 3 defines the final background environment"
                    in task["variants"][0]["promptText"]
                )
            model = DEPTH_MODEL if mode == "depth" else configured_model
            assert task["settings"]["model"] == model
            assert task["siteProfileId"] == source_profile
            if source_profile:
                assert task["settings"]["cutflowSite"]["profileId"] == source_profile
                assert task["settings"]["cutflowSite"]["brandReference"]["shortName"] == "PW"
            assert ("Brand reference: PW / PageWings." in task["variants"][0]["promptText"]) == (
                bool(source_profile) and mode == "recreate"
            )
            assert "pagewings" not in task["variants"][0]["promptText"]
            # A later edit must not change a queued task or its idempotent response.
            changed = await client.patch(
                "/api/v1/admin/wavespeed/config", headers=admin_headers,
                json={"recreateVideoModel": "synthetic/later/video-edit"},
            )
            assert changed.status_code == 200
            replay = await client.post(
                "/api/v1/video-tasks/deep", headers=create_headers, json=payload
            )
            assert replay.json() == task
            recovery = await client.post(
                "/api/v1/submission-recovery/query",
                headers=headers,
                json={"kind": "video", "clientId": "deep-client", "idempotencyKey": "create-deep"},
            )
            assert recovery.json()["state"] == "submitted"
            assert recovery.json()["resourceId"] == task["id"]
            used = await client.post(
                "/api/v1/video-tasks/deep",
                headers={**headers, "Idempotency-Key": "duplicate-upload"},
                json={**payload, "clientTaskId": "another"},
            )
            assert used.status_code == 422
            assert used.json()["detail"]["code"] == "UPLOAD_UNAVAILABLE"
            # Ownership is checked even when an upload's UUID is known.
            denied = await client.post(
                "/api/v1/video-tasks/deep",
                headers={"X-User-Id": settings.dev_admin_id, "Idempotency-Key": "foreign"},
                json={**payload, "clientTaskId": "foreign"},
            )
            assert denied.status_code == 422
        async with app.state.database.session_factory() as session:
            attempts = list(await session.scalars(select(GenerationAttempt)))
            commands = list(await session.scalars(select(GenerationCommand)))
            assert len(attempts) == len(commands) == 1
            assert attempts[0].provider == settings.wavespeed_video_provider_name
            assert attempts[0].provider_model == model
            uploads = list(await session.scalars(select(StagedUpload)))
            assert all(str(upload.claimed_task_id) == task["id"] for upload in uploads)
            usage = await session.scalar(select(AccountUsage))
            assert usage.units == 6
            assert usage.pricing_category == (
                "DEPTH_VIDEO" if mode == "depth" else "RECREATE_DEPTH_VIDEO"
            )
            assert usage.amount_microusd == (300_000 if mode == "depth" else 1_500_000)
            assert task["settings"]["estimatedBillableSeconds"] == (
                6 if mode == "depth" else 12 if flash_edit else 18
            )
            if mode == "recreate":
                assert "@Image 1" in task["variants"][0]["promptText"]
                assert task["settings"]["audio"] == "original"
        # A new runner reconstructs the model and all local inputs from the saved task.
        fake_client = FakeWaveSpeedClient()
        adapter = WaveSpeedVideoAdapter(settings, client=fake_client)
        supervisor = RunnerSupervisor(
            app.state.libtv_adapter,
            app.state.object_store,
            settings,
            kling_adapter=adapter,
        )
        async with app.state.database.session_factory() as session:
            assert await supervisor.run_ready(session) == 1
            assert len(fake_client.submissions) == 1
            submitted_model, submitted_payload, _ = fake_client.submissions[0]
            assert submitted_model == DEPTH_MODEL
            assert "reference.mp4" in submitted_payload["video"]
            assert len(fake_client.uploads) == 1
            if mode == "recreate":
                # A pending depth prediction cannot submit editing or expose a final result.
                assert await supervisor.run_ready(session) == 1
                assert len(fake_client.submissions) == 1
                assert not list(await session.scalars(select(DownloadArtifact)))
                attempt = await session.scalar(select(GenerationAttempt))
                attempt.last_polled_at = None
                await session.commit()
                fake_client.prediction = WaveSpeedPrediction(
                    task_id="prediction-1", status="completed",
                    outputs=("https://cdn.example/extracted-depth.mp4",), error=None,
                    model_id=DEPTH_MODEL, raw_response={"data": {"status": "completed"}},
                )
                assert await supervisor.poll_kling_tasks(session) == 1
                assert len(fake_client.submissions) == 1
                assert not list(await session.scalars(select(DownloadArtifact)))
                # Recover with a new runner; its editing input must be the downloaded
                # depth bytes, not the original blue video, including reference order.
                depth = tmp_path / "synthetic-depth.mp4"
                await asyncio.to_thread(subprocess.run, [
                    "ffmpeg", "-v", "error", "-i", str(reference_video),
                    "-vf", "format=gray", str(depth),
                ], check=True, capture_output=True)

                async def download_depth(*, url, destination):
                    assert url == "https://cdn.example/extracted-depth.mp4"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(depth.read_bytes())
                    return destination

                normalized_media = []
                if flash_edit:
                    upload_media = fake_client.upload_media

                    async def inspect_upload(path):
                        if path.suffix == ".mp4":
                            normalized_media.append(await inspect_reference_video(path))
                        return await upload_media(path)

                    monkeypatch.setattr(fake_client, "upload_media", inspect_upload)
                recovered_adapter = WaveSpeedVideoAdapter(settings, client=fake_client)
                monkeypatch.setattr(recovered_adapter, "download_media", download_depth)
                supervisor = RunnerSupervisor(
                    app.state.libtv_adapter, app.state.object_store, settings,
                    kling_adapter=recovered_adapter,
                )
                assert await supervisor.run_ready(session) == 1
                assert len(fake_client.submissions) == 2
                submitted_model, submitted_payload, _ = fake_client.submissions[1]
                assert submitted_model == model
                assert "depth.mp4" in submitted_payload["video"]
                assert len(fake_client.uploads) == 5
                if flash_edit:
                    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
                    assert normalized_media == [VideoMetadata(pytest.approx(5.2), width, height)]
                    assert not fake_client.uploads[1].exists()
                    receipts = list(await session.scalars(select(GenerationSubmissionAttempt)))
                    assert len(receipts) == 2
                    assert all(receipt.status == "ACCEPTED" for receipt in receipts)
                    assert submitted_payload == {
                        "video": "https://cdn.example/depth.mp4",
                        "prompt": task["variants"][0]["promptText"],
                        "reference_images": submitted_payload["reference_images"],
                        "resolution": "720p",
                        "generate_audio": False,
                    }
                else:
                    assert fake_client.uploads[1].read_bytes() == depth.read_bytes()
                    assert fake_client.uploads[1].read_bytes() != reference_video.read_bytes()
                    assert submitted_payload["duration"] == 6
                command = await session.scalar(select(GenerationCommand))
                state = command.command_manifest_json["depthPipeline"]
                assert state["depthTaskId"] == "prediction-1"
                assert state["replacementTaskId"] == "prediction-1"
                localized = await app.state.object_store.localize(
                    state["depthObjectKey"], Path(tmp_path / "recovered-depth.mp4"),
                )
                assert localized.read_bytes() == depth.read_bytes()
                assert submitted_payload["reference_images"]
                assert submitted_payload["resolution"] == "720p"
            if mode == "recreate":
                assert payload["prompt"] in submitted_payload["prompt"]
                references = submitted_payload["reference_images"]
                image_paths = [path for path in fake_client.uploads if path.suffix != ".mp4"]
                assert len(references) == 3
                # Upload normalization may encode JPEG, so tolerate compression rounding.
                colors = [
                    tuple(Image.open(path).getpixel((0, 0)))
                    for path in image_paths
                ]
                expected_colors = [(255, 0, 0), (0, 128, 0), (0, 0, 255)]
                assert all(
                    all(abs(actual[index] - expected[index]) <= 3 for index in range(3))
                    for actual, expected in zip(
                        colors, expected_colors, strict=True
                    )
                )


@pytest.mark.asyncio
async def test_uploaded_depth_video_skips_extraction_and_submits_replacement_once(
    reference_video, tmp_path, monkeypatch,
):
    """A depth upload is already the depth stage; only the edit model may run."""
    async def available(self):
        return ProviderAvailability("AVAILABLE", True, True)

    async def allow(*args, **kwargs):
        pass

    monkeypatch.setattr(WaveSpeedVideoAdapter, "check_availability", available)
    monkeypatch.setattr(
        "libtv_agent.services.fixed_video_tasks.ensure_provider_submission_allowed",
        allow,
    )
    settings = settings_for(
        tmp_path, wavespeed_enabled=True, wavespeed_api_key="synthetic-test",
    )
    depth_video = tmp_path / "uploaded-depth.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg", "-v", "error", "-i", str(reference_video),
            "-vf", "format=gray", str(depth_video),
        ],
        check=True,
        capture_output=True,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "depth-input-video"}
            uploaded = await client.post(
                "/api/v1/uploads/video", headers=headers,
                files={"file": ("depth.mp4", depth_video.read_bytes(), "video/mp4")},
            )
            assert uploaded.status_code == 201, uploaded.text
            image_bytes = io.BytesIO()
            Image.new("RGB", (256, 384), "red").save(image_bytes, format="PNG")
            image = await client.post(
                "/api/v1/uploads",
                headers={**headers, "Idempotency-Key": "depth-input-image"},
                files={"file": ("subject.png", image_bytes.getvalue(), "image/png")},
            )
            assert image.status_code == 201, image.text
            payload = {
                "clientTaskId": "depth-input-client",
                "title": "Depth input replacement",
                "referenceVideoUploadId": uploaded.json()["id"],
                "referenceVideoType": "depth",
                "mode": "recreate",
                "subjectImages": [{"uploadId": image.json()["id"], "order": 1}],
                "prompt": "Use @Image 1 for the target garment",
                "aspectRatio": "9:16",
                "paidSubmissionConsent": True,
            }
            result = await client.post(
                "/api/v1/video-tasks/deep",
                headers={**headers, "Idempotency-Key": "depth-input-create"},
                json=payload,
            )
            assert result.status_code == 201, result.text
            task = result.json()
            assert task["settings"]["referenceVideoType"] == "depth"
            assert task["settings"]["deepVideoPipeline"] == "depth-input-v1"
            assert task["settings"]["deepVideoStage"] == "replacement"
            assert task["settings"]["estimatedBillableSeconds"] == 12
            assert task["compiledPlan"]["stages"] == [settings.wavespeed_recreate_video_model]

            fake_client = FakeWaveSpeedClient()
            supervisor = RunnerSupervisor(
                app.state.libtv_adapter, app.state.object_store, settings,
                kling_adapter=WaveSpeedVideoAdapter(settings, client=fake_client),
            )
            async with app.state.database.session_factory() as session:
                assert await supervisor.run_ready(session) == 1
                assert len(fake_client.submissions) == 1
                submitted_model, submitted_payload, _ = fake_client.submissions[0]
                assert submitted_model == settings.wavespeed_recreate_video_model
                assert submitted_payload["video"] == "https://cdn.example/reference.mp4"
                command = await session.scalar(select(GenerationCommand))
                state = command.command_manifest_json["depthPipeline"]
                assert state["version"] == "depth-input-v1"
                assert state["stage"] == "replacement"
                assert state["depthInput"] == "uploaded"
                assert "depthTaskId" not in state


@pytest.mark.asyncio
async def test_video_upload_rejects_invalid_content_and_releases_claim(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "invalid"}
            for _ in range(2):
                result = await client.post(
                    "/api/v1/uploads/video",
                    headers=headers,
                    files={"file": ("fake.mp4", b"not-video", "video/mp4")},
                )
                assert result.status_code == 422
            payload = {
                "clientTaskId": "missing",
                "title": "Missing",
                "mode": "depth",
                "referenceVideoUploadId": str(uuid.uuid4()),
                "paidSubmissionConsent": True,
            }
            for _ in range(2):
                result = await client.post(
                    "/api/v1/video-tasks/deep", headers=headers, json=payload
                )
                assert result.status_code == 422
                assert result.json()["detail"]["code"] == "UPLOAD_UNAVAILABLE"
            status = await client.get("/api/v1/video-tasks/deep/status", headers=headers)
            assert not status.json()["enabled"]
            assert not list((tmp_path / "artifacts" / "staging").glob("*"))
