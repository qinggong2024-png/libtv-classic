import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase
from libtv_agent.domain.models import GenerationAttempt, GenerationCommand, ParentTask
from libtv_agent.services.tasks import _build_task_timing
from test_deep_video import reference_video as reference_video
from test_task_timing import START, STOP, task
from test_workflow_integration import (
    OTHER_USER_ID,
    create_draft_task,
    running_app,
    user_headers,
)


@pytest.mark.parametrize("stage", ["depth", "replacement"])
@pytest.mark.parametrize(
    "status",
    [
        AttemptStatus.RUNNING,
        AttemptStatus.FAILED_TERMINAL,
        AttemptStatus.UNKNOWN_AFTER_SUBMIT,
        AttemptStatus.SUCCEEDED,
    ],
)
def test_split_depth_timing(stage, status):
    parent = task(
        ParentTaskPhase.GENERATING
        if status == AttemptStatus.RUNNING
        else ParentTaskPhase.ACTION_REQUIRED
    )
    parent.settings_json = {"deepVideoPipeline": "depth-first-v1"}
    end = STOP + timedelta(seconds=9)
    attempt = GenerationAttempt(
        id=uuid.uuid4(),
        status=status,
        submitted_at=START,
        updated_at=end,
        completed_at=end if status == AttemptStatus.SUCCEEDED else None,
    )
    state = {"stage": stage, "depthSubmittedAt": START.isoformat()}
    if stage == "replacement":
        state.update(depthCompletedAt=STOP.isoformat(), replacementSubmittedAt=STOP.isoformat())
    stages = {
        s.key: s
        for s in _build_task_timing(
            parent, {parent.variants[0].id: attempt}, {}, None, state
        ).stages
    }
    assert "GENERATION" not in stages
    if stage == "replacement":
        assert stages["DEPTH_EXTRACTION"].status == "COMPLETED"
        assert stages["DEPTH_EXTRACTION"].duration_seconds == 6
        assert stages["REPLACEMENT"].duration_seconds == (
            None if status == AttemptStatus.RUNNING else 9
        )
    else:
        assert stages["REPLACEMENT"].status == "PENDING"
    if status != AttemptStatus.RUNNING:
        assert not any(s.status == "ACTIVE" for s in stages.values())


@pytest.mark.asyncio
async def test_historical_depth_preview_download_and_owner(tmp_path: Path, reference_video: Path):
    async with running_app(tmp_path / "app") as (client, app):
        draft = await create_draft_task(client, "saved-depth")
        async with app.state.database.session_factory() as session:
            parent = await session.get(ParentTask, uuid.UUID(draft["id"]))
            parent.workflow_kind = "DEEP_VIDEO"
            parent.settings_json = {"deepVideoPipeline": "depth-first-v1"}
            parent.phase = ParentTaskPhase.ACTION_REQUIRED
            attempt = GenerationAttempt(
                variant_id=uuid.UUID(draft["variants"][0]["id"]),
                status=AttemptStatus.FAILED_TERMINAL,
            )
            session.add(attempt)
            await session.flush()
            key = await app.state.object_store.put_file(
                reference_video, "saved/depth.mp4", "video/mp4"
            )
            command = GenerationCommand(
                attempt_id=attempt.id,
                command_manifest_json={
                    "depthPipeline": {
                        "stage": "replacement",
                        "depthObjectKey": key,
                        "depthSubmittedAt": START.isoformat(),
                        "depthCompletedAt": STOP.isoformat(),
                    }
                },
            )
            session.add(command)
            await session.commit()
            command_id = command.id
        read = (await client.get(f"/api/v1/tasks/{draft['id']}", headers=user_headers())).json()
        assert read["depthVideoResult"]["downloadId"] == str(command_id)
        assert read["variants"][0]["downloadArtifactId"] is None
        assert "depthObjectKey" not in str(read)
        for suffix in ["", "/url", "/thumbnail"]:
            path = f"/api/v1/downloads/{command_id}{suffix}"
            response = await client.get(path, headers=user_headers())
            assert response.status_code == 200, response.text[:200]
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(path, headers=user_headers(user_id=OTHER_USER_ID))
            ).status_code == 404
            if suffix == "":
                assert response.content == await asyncio.to_thread(reference_video.read_bytes)
                assert "-depth.mp4" in response.headers["content-disposition"]
        await app.state.object_store.delete(key)
        assert (
            await client.get(f"/api/v1/downloads/{command_id}", headers=user_headers())
        ).status_code == 410
