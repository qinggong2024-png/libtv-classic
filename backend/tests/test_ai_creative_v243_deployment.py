"""Accepted classic route: offline provider replay, no billable calls."""

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import Base, GenerationVariant
from libtv_agent.services import ai_creative_planner
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_PLANNER_VERSION,
    CreativeDirectorResponse,
    plan_creative_video,
    production_v25_enabled,
)
from libtv_agent.services.fixed_video_tasks import _reference_role_prompt
from libtv_agent.services.kling_preparation import prepare_kling_task
from libtv_agent.services.tasks import get_task
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from test_ai_creative_planner import _two_frame_fixture
from test_fixed_template_foundation import png_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [5, 10, 15])
async def test_v243_two_candidates_keep_four_reference_roles_and_token_budget(duration):
    facts, batch = _two_frame_fixture(linked=True)
    first = batch.variants[0]
    second = first.model_copy(update={
        "visual_hook": "She closes her book and reveals the original reading-ghost artwork",
        "creative_idea": "A reader shows her bookish identity through the original garment.",
    })
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=CreativeDirectorResponse(
            observations=facts, variants=[first, second],
        ))

    settings = Settings(
        app_env="test", ai_adapter_mode="openai", openai_model="gpt-6.1-sol",
        openai_api_key=None, ai_creative_v25_enabled=False, _env_file=None,
    )
    assert not production_v25_enabled(settings)
    scenes = [AnalysisImageInput(f"scene-{i}", i, f"scene-url-{i}") for i in (1, 2)]
    products = [
        AnalysisImageInput(f"product-{i}", i, f"product-url-{i}", role="PRODUCT")
        for i in (1, 2)
    ]
    result = await plan_creative_video(
        settings=settings, storyboard_images=scenes, product_images=products,
        reference_role_prompt=_reference_role_prompt(
            storyboard_count=2, subject_image_count=2, element_count=0,
        ),
        creative_brief="", brand_tone="", duration_seconds=duration, variant_count=2,
        experimental_v25=production_v25_enabled(settings),
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
    )
    assert len(calls) == 1
    request = calls[0]
    assert request["text_format"] is CreativeDirectorResponse
    assert request["max_output_tokens"] == 6200
    assert request["reasoning"] == {"effort": "low"}
    content = request["input"][1]["content"]
    assert json.loads(content[0]["text"])["variantCount"] == 2
    assert [x["image_url"] for x in content if x["type"] == "input_image"] == [
        "scene-url-1", "scene-url-2", "product-url-1", "product-url-2",
    ]
    assert len(result) == 2
    assert result[0].prompt != result[1].prompt
    assert CREATIVE_PLANNER_VERSION == "ai-multimodal-director-v2.4.3-evidence-paced"
    for variant in result:
        assert variant.planner_version == CREATIVE_PLANNER_VERSION
        assert variant.beats[-1]["endSeconds"] == duration
        assert len(variant.prompt) <= 3072
        assert "@image_3" in variant.prompt and "@image_4" in variant.prompt
        assert "[@image_3," not in variant.prompt
        assert "[@image_4," not in variant.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_cut", [False, True])
async def test_http_task_routes_real_v243_compiler_and_persists_both_candidates(
    tmp_path, monkeypatch, auto_cut,
):
    """Replay only the upstream response; use the real HTTP/task/compiler chain."""
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "pagewings", replace(
        SITE_STYLE_PROFILES["pagewings"], status="active",
    ))
    facts, batch = _two_frame_fixture(linked=True)
    second = batch.variants[0].model_copy(update={
        "visual_hook": "Her open book reveals the reading-ghost graphic from another angle",
    })
    captured = []

    async def upstream(*args, **kwargs):
        captured.append((args, kwargs))
        return CreativeDirectorResponse(observations=facts, variants=[*batch.variants, second])

    monkeypatch.setattr(ai_creative_planner, "_parse_provider", upstream)
    monkeypatch.setattr(
        "libtv_agent.api.video_tasks.schedule_subject_element_job", lambda *a, **kw: None,
    )
    settings = Settings(
        app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}",
        object_store_mode="local", artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner", kling_admin_config_file=tmp_path / "kling.json",
        ai_adapter_mode="openai", openai_api_key="offline-response-replay-only",
        openai_model="gpt-6.1-sol", ai_creative_v25_enabled=False,
        kling_provider_backend="cli", kling_cli_enabled=False,
        auto_run_fake_commands=False, _env_file=None,
    )
    database = Database(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await database.dispose()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            status = (await client.get("/api/v1/system/status")).json()
            assert status["creativePlanner"] == {
                "version": CREATIVE_PLANNER_VERSION, "v25Enabled": False,
            }
            scenes, products = [], []
            headers = {"X-User-Id": settings.dev_user_id}
            for i, color in enumerate(("#123456", "#654321", "#ff0000", "#00ff00")):
                upload = await client.post(
                    "/api/v1/uploads", headers={**headers, "Idempotency-Key": f"v243-ref-{i}"},
                    files={"file": (f"reference-{i}.png", png_bytes(color), "image/png")},
                )
                assert upload.status_code == 201
                (scenes if i < 2 else products).append({
                    "uploadId": upload.json()["id"], "order": i % 2 + 1,
                })
            accepted = await client.post(
                "/api/v1/video-tasks", headers={**headers, "Idempotency-Key": "v243-task"},
                json={
                    "clientTaskId": "v243-two-candidates", "title": "Offline V2.4.3 acceptance",
                    "storyboards": scenes, "subjectImages": products,
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10, "resultCount": 2,
                    "autoSubmitToCutflow": auto_cut, "paidSubmissionConsent": True,
                },
            )
            assert accepted.status_code == 201, accepted.text
            task_id = accepted.json()["id"]
            if accepted.json()["phase"] == "ANALYZING":
                from libtv_agent.services.async_video_submission import (
                    process_automatic_video_submission,
                )
                await process_automatic_video_submission(
                    app.state.database, settings, app.state.kling_adapter,
                    app.state.object_store, uuid.UUID(task_id),
                )
            result = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            assert result.status_code == 200
            task = result.json()
            assert task["compiledPlan"]["compiler"] == CREATIVE_PLANNER_VERSION
            assert len(captured) == 1
            assert captured[0][1]["experimental_v25"] is False
            assert len(task["variants"]) == 2
            assert task["variants"][0]["promptText"] != task["variants"][1]["promptText"]
            video_requests = []

            class OfflineVideoPreflight:
                def get_provider_name(self):
                    return settings.active_kling_provider_name

                async def check_availability(self):
                    return SimpleNamespace(available=True)

                async def validate_request(self, request):
                    video_requests.append(request)
                    return request.model

            async with app.state.database.session_factory() as session:
                stored_task = await get_task(
                    session, uuid.UUID(settings.dev_user_id), uuid.UUID(task_id),
                )
                await prepare_kling_task(
                    session, OfflineVideoPreflight(), app.state.object_store,
                    stored_task, settings,
                )
            assert len(video_requests) == 2
            for variant, compiled in zip(
                task["variants"], task["compiledPlan"]["variants"], strict=True,
            ):
                assert variant["promptText"] == compiled["master_prompt"]
                assert len(variant["promptText"]) <= 3072
                async with app.state.database.session_factory() as session:
                    stored = await session.get(GenerationVariant, uuid.UUID(variant["id"]))
                    config = stored.generation_config_json
                    assert config["autoSubmitToCutflow"] is auto_cut
                    assert config["storyboardImageCount"] == 2
                    assert config["productReferenceImageCount"] == 2
            for request, variant in zip(video_requests, task["variants"], strict=True):
                assert request.prompt == variant["promptText"]
                assert request.model == "kling-video-v3_0_omni"
                assert request.duration == 10
                assert len(request.image_paths) == 2
                assert len(request.product_reference_paths) == 2
                assert set(request.image_paths).isdisjoint(request.product_reference_paths)
