from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import Base
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from test_fixed_template_foundation import png_bytes


@pytest.mark.parametrize("mode", ["automatic", "custom"])
@pytest.mark.parametrize("brief", ["", "镜头缓慢推进，保留衣服纹理。"])
async def test_optional_brief_reaches_every_variant_and_compiled_plan(
    tmp_path, monkeypatch, mode, brief,
):
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active", version="1.0.0-test",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "geeksoutfit", replace(
        SITE_STYLE_PROFILES["geeksoutfit"], status="active", version="1.0.0-test",
    ))
    settings = Settings(
        app_env="test", database_url="sqlite+aiosqlite:///:memory:",
        object_store_mode="local", artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner", kling_admin_config_file=tmp_path / "kling.json",
        kling_cli_enabled=False, _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "brief-upload"}
            uploaded = await client.post("/api/v1/uploads", headers=headers, files={
                "file": ("shot.png", png_bytes(), "image/png"),
            })
            assert uploaded.status_code == 201
            payload = {
                "clientTaskId": "brief-test", "title": "Brief test",
                "storyboards": [{"uploadId": uploaded.json()["id"], "order": 1}],
                "generationMode": mode, "customPrompt": "自然展示服装。",
                "templateId": "ootd_walk", "siteProfileId": "geeksoutfit",
                "resultCount": 2, "creativeBrief": brief, "paidSubmissionConsent": True,
            }
            response = await client.post("/api/v1/video-tasks", headers={
                **headers, "Idempotency-Key": "brief-create",
            }, json=payload)
            assert response.status_code == 201, response.text
            task = response.json()
            prompts = [variant["promptText"] for variant in task["variants"]]
            assert len(prompts) == 2
            if brief:
                assert all(prompt.count(brief) == 1 for prompt in prompts)
                assert task["compiledPlan"]["creativeBrief"] == brief
                assert task["userRequirement"].endswith(brief)
            else:
                assert "creativeBrief" not in task["compiledPlan"]
                assert all("补充要求：" not in prompt for prompt in prompts)
            plan_prompts = (
                [plan["master_prompt"] for plan in task["compiledPlan"]["variants"]]
                if mode == "automatic" else [task["compiledPlan"]["finalPrompt"]] * 2
            )
            assert plan_prompts == prompts
            # Final combined prompt budget applies even when each input fits its own limit.
            payload.update(clientTaskId="too-long", generationMode="custom",
                           customPrompt="展" * 2750, creativeBrief="细" * 600)
            second_upload = await client.post("/api/v1/uploads", headers={
                **headers, "Idempotency-Key": "brief-second-upload",
            }, files={"file": ("shot.png", png_bytes(), "image/png")})
            payload["storyboards"] = [{"uploadId": second_upload.json()["id"], "order": 1}]
            rejected = await client.post("/api/v1/video-tasks", headers={
                **headers, "Idempotency-Key": "brief-too-long",
            }, json=payload)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"]["code"] == "COMPILED_PROMPT_TOO_LONG"
