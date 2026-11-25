"""P0-3: durable, bounded, before-paid Sol transient retries."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase, VariantKind
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask
from libtv_agent.services import fixed_video_tasks
from libtv_agent.services.ai_creative_planner import (
    CreativePlannerError,
    classify_planner_upstream_error,
)
from libtv_agent.services.async_video_submission import (
    RETRY_SCHEDULED,
    pending_automatic_video_ids,
    process_automatic_video_submission,
    schedule_retryable_sol_failure,
)
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from sqlalchemy import update
from test_automatic_plan_recovery import make_factory, planned_task
from test_fixed_template_foundation import png_bytes


def test_plain_async_timeout_is_classified_as_retryable_transport():
    failure = classify_planner_upstream_error(TimeoutError("upstream stalled"))
    assert failure.code == "AI_CREATIVE_REQUEST_TIMEOUT"


@pytest.mark.asyncio
async def test_delayed_retry_is_durable_and_lease_owned(tmp_path, monkeypatch):
    engine, factory = await make_factory(tmp_path, monkeypatch, migrated=True)
    try:
        first = planned_task(heartbeat_age_minutes=1)
        exhausted = planned_task(heartbeat_age_minutes=1, attempt_count=2)
        paid = planned_task(heartbeat_age_minutes=1)
        stolen = planned_task(heartbeat_age_minutes=1)
        async with factory() as session:
            session.add_all([first, exhausted, paid, stolen])
            session.add(GenerationVariant(
                parent_task_id=paid.id, variant_type=VariantKind.STABLE, revision=1,
            ))
            await session.commit()
        async with factory() as session:
            assert await schedule_retryable_sol_failure(
                session, task_id=first.id, run_id=first.analysis_run_id,
                code="AI_CREATIVE_RATE_LIMITED",
            )
            for entry, owner in (
                (exhausted, exhausted.analysis_run_id),
                (paid, paid.analysis_run_id),
                (stolen, uuid.uuid4()),
            ):
                assert not await schedule_retryable_sol_failure(
                    session, task_id=entry.id, run_id=owner,
                    code="AI_CREATIVE_RATE_LIMITED",
                )
            assert not await schedule_retryable_sol_failure(
                session, task_id=stolen.id, run_id=stolen.analysis_run_id,
                code="AI_CREATIVE_AUTH_FAILED",
            )
        async with factory() as session:
            loaded = await session.get(ParentTask, first.id)
            assert loaded.phase == ParentTaskPhase.ANALYZING
            assert loaded.analysis_stage == "QUEUED"
            assert loaded.phase_reason_code == RETRY_SCHEDULED
            assert loaded.analysis_run_id is None
            assert loaded.analysis_attempt_count == 1
            assert loaded.analysis_heartbeat_at > datetime.now(UTC).replace(tzinfo=None)
            assert first.id not in await pending_automatic_video_ids(session)
            await session.execute(
                update(ParentTask).where(ParentTask.id == first.id).values(
                    analysis_heartbeat_at=datetime.now(UTC)-timedelta(seconds=1)
                )
            )
            await session.commit()
            assert first.id in await pending_automatic_video_ids(session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transient_sol_failure_recovers_once_then_stops_without_video_calls(
    tmp_path, monkeypatch,
):
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "pagewings", replace(
        SITE_STYLE_PROFILES["pagewings"], status="active",
    ))
    calls = []

    async def transient_planner(**kwargs):
        calls.append(kwargs)
        raise CreativePlannerError(
            "fake transient", code="AI_CREATIVE_RATE_LIMITED",
        )

    monkeypatch.setattr(fixed_video_tasks, "plan_creative_video", transient_planner)
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'retry.db').as_posix()}",
        artifact_root=tmp_path / "artifacts", object_store_mode="local",
        runner_root=tmp_path / "runner",
        kling_admin_config_file=tmp_path / "kling.json",
        ai_adapter_mode="openai", openai_api_key="fake-for-test-only",
        kling_cli_enabled=False, _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            uploaded = await client.post(
                "/api/v1/uploads",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "retry-upload",
                },
                files={"file": ("solo.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201, uploaded.text
            accepted = await client.post(
                "/api/v1/video-tasks",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "retry-task",
                },
                json={
                    "clientTaskId": "retryable-sol", "title": "Reader UGC",
                    "storyboards": [{"uploadId": uploaded.json()["id"], "order": 1}],
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10,
                    "resultCount": 1, "paidSubmissionConsent": True,
                },
            )
            assert accepted.status_code == 201, accepted.text
            task_id = uuid.UUID(accepted.json()["id"])
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, task_id,
            )
            async with app.state.database.session_factory() as session:
                task = await session.get(ParentTask, task_id)
                assert task.phase == ParentTaskPhase.ANALYZING
                assert task.analysis_stage == "QUEUED"
                assert task.phase_reason_code == RETRY_SCHEDULED
                assert task.analysis_attempt_count == 1
                assert task.variants == []
                assert task_id not in await pending_automatic_video_ids(session)
            # A manual/duplicate premature Runner claim cannot defeat the delay.
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, task_id,
            )
            assert len(calls) == 1
            async with app.state.database.session_factory() as session:
                await session.execute(
                    update(ParentTask).where(ParentTask.id == task_id).values(
                        analysis_heartbeat_at=datetime.now(UTC)-timedelta(seconds=1)
                    )
                )
                await session.commit()
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, task_id,
            )
            assert len(calls) == 2
            async with app.state.database.session_factory() as session:
                task = await session.get(ParentTask, task_id)
                assert task.phase == ParentTaskPhase.ACTION_REQUIRED
                assert task.phase_reason_code == "AI_CREATIVE_RATE_LIMITED"
                assert task.analysis_attempt_count == 2
                assert task.variants == []
