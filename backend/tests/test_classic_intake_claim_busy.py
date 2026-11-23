"""A locked SQLite claim must fail before accepting or paying for a new video."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.api import video_tasks
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import ApiIdempotencyRecord, Base, ParentTask
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError


@pytest.mark.asyncio
async def test_sqlite_busy_before_claim_is_definite_rejection(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}",
        artifact_root=tmp_path / "artifacts",
        object_store_mode="local",
        runner_root=tmp_path / "runner",
        ai_adapter_mode="openai",
        openai_api_key="synthetic-not-called",
    )
    app = create_app(settings)

    async def busy_claim(*_args, **_kwargs):
        raise OperationalError("INSERT", {}, RuntimeError("database is locked"))

    monkeypatch.setattr(video_tasks, "load_idempotent_response", busy_claim)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/video-tasks",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "claim-busy-original",
                },
                json={
                    "clientTaskId": str(uuid.uuid4()),
                    "title": "Synthetic intake only",
                    "storyboards": [{"uploadId": str(uuid.uuid4()), "order": 1}],
                    "generationMode": "automatic",
                    "templateId": "ootd_walk",
                    "siteProfileId": "pagewings",
                    "durationSeconds": 10,
                    "resultCount": 1,
                    "paidSubmissionConsent": True,
                },
            )
            assert response.status_code == 429, response.text
            assert response.json()["detail"]["code"] == "SUBMISSION_NOT_ACCEPTED_DB_BUSY"
            async with app.state.database.session_factory() as session:
                assert await session.scalar(
                    select(func.count()).select_from(ParentTask)
                ) == 0
                assert await session.scalar(
                    select(func.count()).select_from(ApiIdempotencyRecord)
                ) == 0
