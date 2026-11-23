"""Release gate: twenty real-schema tasks, concurrent Sol, read load and crash resume.

All content and uploads are synthetic fixtures. Provider task creation is local
only. No network, real Sol, Kling billing, CUT POST or production SQLite.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import Base, GenerationSubmissionAttempt, ParentTask
from libtv_agent.services import fixed_video_tasks
from libtv_agent.services.async_video_submission import (
    mark_interrupted_automatic_plans,
    process_automatic_video_submission,
)
from libtv_agent.services.sol_plan_checkpoint import CHECKPOINT_KEY
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from sqlalchemy import func, select
from test_ai_creative_planner import compile_fixture
from test_fixed_template_foundation import png_bytes


@pytest.mark.asyncio
async def test_twenty_synthetic_plans_with_three_workers_and_crash_recovery(
    tmp_path, monkeypatch,
):
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "pagewings", replace(
        SITE_STYLE_PROFILES["pagewings"], status="active",
    ))
    calls = 0

    async def synthetic_sol(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return (compile_fixture(),)

    monkeypatch.setattr(fixed_video_tasks, "plan_creative_video", synthetic_sol)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'prod-shaped.db').as_posix()}",
        artifact_root=tmp_path / "artifacts",
        object_store_mode="local",
        runner_root=tmp_path / "runner",
        kling_admin_config_file=tmp_path / "kling.json",
        ai_adapter_mode="openai",
        openai_api_key="local-fake-never-used",
        kling_cli_enabled=False,
    )
    app = create_app(settings)
    ids = []
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Each task owns its own uploaded image. Only the local upload API
            # is used; nothing reaches an external image or video service.
            for index in range(20):
                upload = await client.post(
                    "/api/v1/uploads",
                    headers={
                        "X-User-Id": settings.dev_user_id,
                        "Idempotency-Key": f"release-lab-upload-{index}",
                    },
                    files={
                        "file": (
                            f"fixture-{index}.png",
                            png_bytes(), "image/png",
                        )
                    },
                )
                assert upload.status_code == 201, (index, upload.text)
                task = await client.post(
                    "/api/v1/video-tasks",
                    headers={
                        "X-User-Id": settings.dev_user_id,
                        "Idempotency-Key": f"release-lab-task-{index}",
                    },
                    json={
                        "clientTaskId": f"release-lab-{index}",
                        "title": f"synthetic UGC {index}",
                        "storyboards": [{
                            "uploadId": upload.json()["id"], "order": 1,
                        }],
                        "generationMode": "automatic",
                        "templateId": "ootd_walk",
                        "siteProfileId": "pagewings",
                        "durationSeconds": 10,
                        "resultCount": 1,
                        "paidSubmissionConsent": True,
                    },
                )
                assert task.status_code == 201, (index, task.text)
                ids.append(uuid.UUID(task.json()["id"]))
            # Kill two workers immediately after durable pre-paid checkpoint.
            saved = fixed_video_tasks.save_plan_checkpoint
            crash_ids = set(ids[:2])

            async def injected_checkpoint(*args, **kwargs):
                ok = await saved(*args, **kwargs)
                if ok and kwargs["task_id"] in crash_ids:
                    raise asyncio.CancelledError()
                return ok

            monkeypatch.setattr(
                fixed_video_tasks, "save_plan_checkpoint", injected_checkpoint,
            )
            sem = asyncio.Semaphore(3)
            crashed = []

            async def worker(task_id):
                async with sem:
                    try:
                        await process_automatic_video_submission(
                            app.state.database, settings, app.state.kling_adapter,
                            app.state.object_store, task_id,
                        )
                    except asyncio.CancelledError:
                        crashed.append(task_id)

            async def reader_load():
                for index in range(35):
                    item = ids[index % len(ids)]
                    response = await client.get(
                        f"/api/v1/tasks/{item}",
                        headers={"X-User-Id": settings.dev_user_id},
                    )
                    assert response.status_code == 200
                    await asyncio.sleep(0.003)

            await asyncio.gather(
                *(worker(task_id) for task_id in ids),
                reader_load(),
            )
            assert set(crashed) == crash_ids
            assert calls == 20, "one paid-planner invocation per new task"
            async with app.state.database.session_factory() as session:
                for task_id in crash_ids:
                    task = await session.get(ParentTask, task_id)
                    assert task.phase == ParentTaskPhase.ANALYZING
                    assert CHECKPOINT_KEY in task.compiled_plan_json
                assert await mark_interrupted_automatic_plans(
                    session, older_than_minutes=0,
                ) == 2
            monkeypatch.setattr(
                fixed_video_tasks, "save_plan_checkpoint", saved,
            )
            # Reclaim after simulated service restart; must reuse the original
            # Sol checkpoint and never add another Sol call/paid POST.
            for task_id in crash_ids:
                await process_automatic_video_submission(
                    app.state.database, settings, app.state.kling_adapter,
                    app.state.object_store, task_id,
                )
            async with app.state.database.session_factory() as session:
                rows = list(await session.scalars(
                    select(ParentTask).where(ParentTask.id.in_(ids))
                ))
                assert len(rows) == 20
                # This is a *no-provider* test environment: video capabilities
                # intentionally fail closed at the paid boundary. Completion
                # means every Sol plan was compiled and either awaits normal
                # confirmation or has the expected fake-provider rejection.
                assert all(row.analysis_stage == "COMPLETED" for row in rows), [
                    (row.client_task_id, str(row.phase), row.phase_reason_code)
                    for row in rows
                ]
                assert all(
                    row.phase != ParentTaskPhase.ACTION_REQUIRED
                    or row.phase_reason_code == "KLING_CAPABILITY_UNSUPPORTED"
                    for row in rows
                )
                assert all(
                    not (row.compiled_plan_json or {}).get(CHECKPOINT_KEY)
                    for row in rows
                )
                external_submissions = await session.scalar(
                    select(func.count()).select_from(GenerationSubmissionAttempt)
                )
                assert external_submissions == 0
            assert calls == 20, "workers must reuse Sol output after restart"
