"""The classic HTTP intake gate never cancels an accepted task or a paid attempt."""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import ApiIdempotencyRecord, Base, ParentTask
from libtv_agent.services.classic_admission_gate import (
    PAUSED_CODE,
    new_video_admission_paused,
)
from sqlalchemy import func, select
from test_fixed_template_foundation import png_bytes

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "classic_new_video_admission.py"


def _operator():
    spec = importlib.util.spec_from_file_location("classic_new_video_admission_test", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_file_is_idempotent_and_never_removes_unknown_flag(tmp_path):
    control = _operator()
    storage = tmp_path / "classic-lan"
    (storage / "runtime").mkdir(parents=True)
    assert not control.read_status(storage)["newVideoAdmissionsPaused"]
    assert control.set_admissions(storage, pause=True)["newVideoAdmissionsPaused"]
    assert control.set_admissions(storage, pause=True)["newVideoAdmissionsPaused"]
    assert control.set_admissions(storage, pause=False)["newVideoAdmissionsPaused"] is False
    control.pause_file(storage).write_text("foreign-operator-file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unrecognized"):
        control.set_admissions(storage, pause=False)
    assert control.pause_file(storage).exists()


def test_unreadable_flag_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "pause.flag"
    settings = Settings(
        _env_file=None, app_env="test", classic_new_video_pause_file=path,
    )
    native_stat = Path.stat

    def stat_with_error(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic access denied")
        return native_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_error)
    assert new_video_admission_paused(settings)


def test_operator_refuses_to_claim_pause_when_running_api_is_old(monkeypatch):
    control = _operator()

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _count):
            return json.dumps({
                "gateVersion": 1,
                "configured": False,
                "deploymentId": control.EXPECTED_DEPLOYMENT,
                "scope": "new-video-create-only",
            }).encode()

    class Opener:
        def open(self, _url, timeout):
            assert timeout <= 3
            return Response()

    monkeypatch.setattr(control, "build_opener", lambda *_: Opener())
    with pytest.raises(RuntimeError, match="does not support"):
        control.verify_live_api_gate()


@pytest.mark.asyncio
async def test_running_api_can_pause_new_video_posts_without_affecting_existing_tasks(
    tmp_path,
):
    storage = tmp_path / "classic-lan"
    (storage / "runtime").mkdir(parents=True)
    control = _operator()
    settings = Settings(
        _env_file=None, app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}",
        artifact_root=tmp_path / "artifact",
        object_store_mode="local",
        runner_root=tmp_path / "runner",
        ai_adapter_mode="openai",
        openai_api_key="synthetic-never-called",
        kling_cli_enabled=False,
        classic_new_video_pause_file=control.pause_file(storage),
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        headers = {"X-User-Id": settings.dev_user_id}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            upload = await client.post(
                "/api/v1/uploads",
                headers={**headers, "Idempotency-Key": "staged-first"},
                files={"file": ("fixture.png", png_bytes(), "image/png")},
            )
            assert upload.status_code == 201, upload.text
            payload = {
                "clientTaskId": "canary-a", "title": "Test UGC",
                "storyboards": [{"uploadId": upload.json()["id"], "order": 1}],
                "generationMode": "automatic",
                "templateId": "ootd_walk", "siteProfileId": "pagewings",
                "durationSeconds": 10, "resultCount": 1,
                "paidSubmissionConsent": True,
            }
            # Control path is not configured in other nonclassic deployments.
            assert not new_video_admission_paused(settings)
            live_status = await client.get(
                "/api/v1/system/classic-new-video-admission",
            )
            assert live_status.status_code == 200
            assert live_status.json()["configured"] is True
            assert live_status.json()["newVideoAdmissionsPaused"] is False
            first = await client.post(
                "/api/v1/video-tasks",
                headers={**headers, "Idempotency-Key": "first-task"},
                json=payload,
            )
            assert first.status_code == 201, first.text
            existing_id = first.json()["id"]

            # Operator switches the filesystem flag while the API stays alive.
            assert control.set_admissions(storage, pause=True)[
                "newVideoAdmissionsPaused"
            ]
            live_status = await client.get(
                "/api/v1/system/classic-new-video-admission",
            )
            assert live_status.json()["newVideoAdmissionsPaused"] is True
            post = await client.post(
                "/api/v1/video-tasks",
                headers={**headers, "Idempotency-Key": "blocked-task"},
                json={**payload, "clientTaskId": "canary-blocked"},
            )
            assert post.status_code == 503
            assert post.json()["detail"]["code"] == PAUSED_CODE
            assert post.headers["Retry-After"] == "60"
            # A distinct deep-video path is equally subject to new intake.
            deep = await client.post(
                "/api/v1/video-tasks/deep",
                headers={**headers, "Idempotency-Key": "blocked-deep"},
                json={
                    "clientTaskId": "deep-blocked", "title": "Deep demo",
                    "referenceVideoUploadId": str(uuid.uuid4()),
                    "mode": "depth", "paidSubmissionConsent": True,
                },
            )
            assert deep.status_code == 503, deep.text
            existing = await client.get(
                f"/api/v1/tasks/{existing_id}", headers=headers,
            )
            assert existing.status_code == 200
            assert existing.json()["id"] == existing_id
            # Intake freeze must still allow reconciliation of the ORIGINAL
            # request without opening any new paid submission path.
            recovered = await client.post(
                "/api/v1/submission-recovery/query",
                headers=headers,
                json={
                    "kind": "video",
                    "clientId": payload["clientTaskId"],
                    "idempotencyKey": "first-task",
                },
            )
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()["state"] == "submitted"
            assert recovered.json()["resourceId"] == existing_id
            # POST replay stays closed during maintenance. Read-only
            # reconciliation is the supported path while intake is paused.
            paused_replay = await client.post(
                "/api/v1/video-tasks",
                headers={**headers, "Idempotency-Key": "first-task"},
                json=payload,
            )
            assert paused_replay.status_code == 503
            assert paused_replay.json()["detail"]["code"] == PAUSED_CODE
            # Media upload and status use remain available for current users.
            upload_after = await client.post(
                "/api/v1/uploads",
                headers={**headers, "Idempotency-Key": "upload-after-pause"},
                files={"file": ("more.png", png_bytes(), "image/png")},
            )
            assert upload_after.status_code == 201
            async with app.state.database.session_factory() as session:
                assert await session.scalar(
                    select(func.count()).select_from(ApiIdempotencyRecord).where(
                        ApiIdempotencyRecord.idempotency_key.in_(
                            ["blocked-task", "blocked-deep"]
                        )
                    )
                ) == 0, "rejected intake must never consume an idempotency claim"
                assert await session.scalar(
                    select(func.count()).select_from(ParentTask)
                ) == 1
            assert not control.set_admissions(storage, pause=False)[
                "newVideoAdmissionsPaused"
            ]
            # Reusing the original identity after reopening must return the
            # existing accepted task instead of generating a second task.
            replay = await client.post(
                "/api/v1/video-tasks",
                headers={**headers, "Idempotency-Key": "first-task"},
                json=payload,
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["id"] == existing_id
            allowed_again = await client.post(
                "/api/v1/video-tasks",
                headers={**headers, "Idempotency-Key": "unpaused-task"},
                json={
                    **payload,
                    "clientTaskId": "canary-unpaused",
                    "storyboards": [{"uploadId": upload_after.json()["id"], "order": 1}],
                },
            )
            assert allowed_again.status_code == 201, allowed_again.text
            async with app.state.database.session_factory() as session:
                assert await session.scalar(
                    select(func.count()).select_from(ParentTask)
                ) == 2
