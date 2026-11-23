"""P0 V2: durable Sol checkpoints and paid-boundary fencing.

All tests use local SQLite, fake AI output and the existing dry-run adapter.
"""
import asyncio
import sqlite3
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase, VariantKind
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask
from libtv_agent.services import fixed_video_tasks
from libtv_agent.services.ai_creative_planner import CompiledCreativeVariant
from libtv_agent.services.async_video_submission import (
    mark_interrupted_automatic_plans,
    process_automatic_video_submission,
)
from libtv_agent.services.sol_plan_checkpoint import (
    CHECKPOINT_KEY,
    restored_plan,
    save_plan_checkpoint,
)
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from sqlalchemy.exc import OperationalError
from test_ai_creative_planner import compile_fixture
from test_automatic_plan_recovery import make_factory, planned_task
from test_fixed_template_foundation import png_bytes


def fixture_variant() -> CompiledCreativeVariant:
    return CompiledCreativeVariant(
        prompt="Handheld reference-accurate product display",
        creative_idea="A reader reveals the exact outfit print naturally",
        visual_hook="A brisk reveal",
        buyer_reason="A recognizable reader identity",
        beats=({"action": "lower a book"},),
        facts=({"person_count_verified": True},),
        planner_version="isolated-test-v1",
        request_attempts=1,
    )


@pytest.mark.asyncio
async def test_checkpoint_round_trip_and_input_fingerprint(tmp_path, monkeypatch):
    engine, factory = await make_factory(tmp_path, monkeypatch, migrated=True)
    try:
        task = planned_task(heartbeat_age_minutes=1)
        async with factory() as session:
            session.add(task)
            await session.commit()
        async with factory() as session:
            assert await save_plan_checkpoint(
                session, task_id=task.id, run_id=task.analysis_run_id,
                fingerprint="same-input-hash", variants=[fixture_variant()],
            )
        async with factory() as session:
            loaded = await session.get(ParentTask, task.id)
            recovered = restored_plan(
                loaded, fingerprint="same-input-hash", variant_count=1,
            )
            assert recovered == (fixture_variant(),)
            assert restored_plan(
                loaded, fingerprint="different-input-hash", variant_count=1,
            ) is None
            assert restored_plan(
                loaded, fingerprint="same-input-hash", variant_count=2,
            ) is None
            # The checkpoint is pre-paid; no GenerationVariant/attempt exists.
            assert loaded.phase == ParentTaskPhase.ANALYZING
            assert loaded.analysis_stage == "RUNNING"
            assert loaded.variants == []
            assert loaded.compiled_plan_json[CHECKPOINT_KEY]["fingerprint"] == "same-input-hash"
            # Simulated killed process: reclaim lease without losing checkpoint.
            assert await mark_interrupted_automatic_plans(
                session, older_than_minutes=0,
            ) == 1
        async with factory() as session:
            queued = await session.get(ParentTask, task.id)
            assert queued.phase == ParentTaskPhase.ANALYZING
            assert queued.analysis_stage == "QUEUED"
            assert restored_plan(
                queued, fingerprint="same-input-hash", variant_count=1,
            ) == (fixture_variant(),)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_rejects_stale_owner_paid_boundary_and_invalid_data(
    tmp_path, monkeypatch,
):
    engine, factory = await make_factory(tmp_path, monkeypatch)
    try:
        owner_lost = planned_task()
        committed = planned_task()
        preparing = planned_task()
        preparing.phase = ParentTaskPhase.PREPARING
        cancelled = planned_task()
        cancelled.phase = ParentTaskPhase.CANCELLED
        async with factory() as session:
            session.add_all([owner_lost, committed, preparing, cancelled])
            session.add(GenerationVariant(
                parent_task_id=committed.id, variant_type=VariantKind.STABLE, revision=1,
            ))
            await session.commit()
        for item, token in (
            (owner_lost, uuid.uuid4()),
            (committed, committed.analysis_run_id),
            (preparing, preparing.analysis_run_id),
            (cancelled, cancelled.analysis_run_id),
        ):
            async with factory() as session:
                assert not await save_plan_checkpoint(
                    session, task_id=item.id, run_id=token,
                    fingerprint="safe", variants=[fixture_variant()],
                )
        async with factory() as session:
            for item in (owner_lost, committed, preparing, cancelled):
                persisted = await session.get(ParentTask, item.id)
                assert not (persisted.compiled_plan_json or {}).get(CHECKPOINT_KEY)
        sample = planned_task()
        sample.compiled_plan_json = {
            CHECKPOINT_KEY: {
                "version": 1, "fingerprint": "hash",
                "variants": [{**fixture_variant().__dict__, "facts": [{"bad": True}]}],
            },
        }
        assert restored_plan(sample, fingerprint="hash", variant_count=1) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_crash_after_sol_checkpoint_reuses_plan_without_second_ai_call(
    tmp_path, monkeypatch,
):
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "pagewings", replace(
        SITE_STYLE_PROFILES["pagewings"], status="active",
    ))
    ai_calls = []

    async def fake_planner(**kwargs):
        ai_calls.append(kwargs)
        return (compile_fixture(),)

    monkeypatch.setattr(fixed_video_tasks, "plan_creative_video", fake_planner)
    saved = fixed_video_tasks.save_plan_checkpoint

    async def crash_after_checkpoint(*args, **kwargs):
        assert await saved(*args, **kwargs)
        # Killed worker: this is not an ordinary AI validation failure.
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        fixed_video_tasks, "save_plan_checkpoint", crash_after_checkpoint,
    )
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}",
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
            upload = await client.post(
                "/api/v1/uploads",
                headers={"X-User-Id": settings.dev_user_id,
                         "Idempotency-Key": "v2-upload"},
                files={"file": ("solo.png", png_bytes(), "image/png")},
            )
            assert upload.status_code == 201, upload.text
            accepted = await client.post(
                "/api/v1/video-tasks",
                headers={"X-User-Id": settings.dev_user_id,
                         "Idempotency-Key": "v2-task"},
                json={
                    "clientTaskId": "v2-crash-test", "title": "Reader UGC",
                    "storyboards": [{"uploadId": upload.json()["id"], "order": 1}],
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10,
                    "resultCount": 1, "paidSubmissionConsent": True,
                },
            )
            assert accepted.status_code == 201, accepted.text
            task_id = uuid.UUID(accepted.json()["id"])
            with pytest.raises(asyncio.CancelledError):
                await process_automatic_video_submission(
                    app.state.database, settings, app.state.kling_adapter,
                    app.state.object_store, task_id,
                )
            assert len(ai_calls) == 1
            async with app.state.database.session_factory() as session:
                crashed = await session.get(ParentTask, task_id)
                assert crashed.phase == ParentTaskPhase.ANALYZING
                assert crashed.analysis_stage == "RUNNING"
                assert CHECKPOINT_KEY in crashed.compiled_plan_json
                assert crashed.variants == []
                assert await mark_interrupted_automatic_plans(
                    session, older_than_minutes=0,
                ) == 1

            monkeypatch.setattr(
                fixed_video_tasks, "save_plan_checkpoint", saved,
            )
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, task_id,
            )
            assert len(ai_calls) == 1, "checkpoint must avoid a repeated Sol call"
            response = await client.get(
                f"/api/v1/tasks/{task_id}",
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert response.status_code == 200
            assert response.json()["variants"][0]["promptText"] == compile_fixture().prompt
            assert response.json()["compiledPlan"]["compiler"]
            # Dry-run only: this test never invokes a real provider.


@pytest.mark.asyncio
async def test_bounded_sqlite_busy_retry_cannot_repeat_a_paid_submission():
    class LockedOnce:
        def __init__(self):
            self.attempts = 0
            self.rollbacks = 0
            self.commits = 0

        async def execute(self, _statement):
            self.attempts += 1
            if self.attempts == 1:
                raise OperationalError(
                    "UPDATE parent_task", {},
                    sqlite3.OperationalError("database is locked"),
                )
            return SimpleNamespace(rowcount=1)

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            self.commits += 1

    stub = LockedOnce()
    assert await save_plan_checkpoint(
        stub, task_id=uuid.uuid4(), run_id=uuid.uuid4(),
        fingerprint="same", variants=[fixture_variant()],
    )
    assert (stub.attempts, stub.rollbacks, stub.commits) == (2, 1, 1)


@pytest.mark.asyncio
async def test_non_busy_sqlite_error_does_not_retry():
    class PermanentError:
        def __init__(self):
            self.attempts = 0
            self.rollbacks = 0

        async def execute(self, _statement):
            self.attempts += 1
            raise OperationalError(
                "UPDATE parent_task", {},
                sqlite3.OperationalError("disk I/O error"),
            )

        async def rollback(self):
            self.rollbacks += 1

    stub = PermanentError()
    with pytest.raises(OperationalError):
        await save_plan_checkpoint(
            stub, task_id=uuid.uuid4(), run_id=uuid.uuid4(),
            fingerprint="same", variants=[fixture_variant()],
        )
    assert (stub.attempts, stub.rollbacks) == (1, 1)
