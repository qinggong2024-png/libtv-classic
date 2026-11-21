import io
import uuid
from pathlib import Path

import pytest
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import DownloadArtifact, GenerationAttempt, ParentTask
from PIL import Image
from test_deep_video import reference_video as reference_video
from test_workflow_integration import (
    OTHER_USER_ID,
    create_draft_task,
    running_app,
    user_headers,
)


@pytest.mark.asyncio
async def test_result_thumbnail_uses_output_video_and_checks_owner(
    tmp_path: Path, reference_video: Path, monkeypatch
) -> None:
    async with running_app(tmp_path / "app") as (client, app):
        task = await create_draft_task(client, "result-preview")
        artifact_id = uuid.uuid4()
        async with app.state.database.session_factory() as session:
            attempt = GenerationAttempt(variant_id=uuid.UUID(task["variants"][0]["id"]))
            session.add(attempt)
            await session.flush()
            key = await app.state.object_store.put_file(
                reference_video, "outputs/result.mp4", "video/mp4"
            )
            session.add(DownloadArtifact(id=artifact_id, attempt_id=attempt.id, object_key=key))
            await session.commit()
        path = f"/api/v1/downloads/{artifact_id}/thumbnail"
        response = await client.get(path, headers=user_headers())
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (64, 96)
            red, green, blue = image.convert("RGB").getpixel((20, 20))
            assert blue > 200 and red < 30 and green < 30

        async def unexpected_transcode(*_args):
            raise AssertionError("cached thumbnails must not transcode twice")

        monkeypatch.setattr("libtv_agent.api.downloads.media_command", unexpected_transcode)
        assert (await client.get(path, headers=user_headers())).content == response.content
        assert (await client.get(path)).status_code == 401
        other = await client.get(path, headers=user_headers(user_id=OTHER_USER_ID))
        assert other.status_code == 404
        async with app.state.database.session_factory() as session:
            artifact = await session.get(DownloadArtifact, artifact_id)
            artifact.object_key = None
            await session.commit()
        assert (await client.get(path, headers=user_headers())).status_code == 410


@pytest.mark.asyncio
async def test_invalid_result_thumbnail_has_clear_error(tmp_path: Path) -> None:
    async with running_app(tmp_path) as (client, app):
        task = await create_draft_task(client, "invalid-result")
        media = tmp_path / "invalid.mp4"
        media.write_bytes(b"invalid-video")
        artifact_id = uuid.uuid4()
        async with app.state.database.session_factory() as session:
            attempt = GenerationAttempt(variant_id=uuid.UUID(task["variants"][0]["id"]))
            session.add(attempt)
            await session.flush()
            key = await app.state.object_store.put_file(media, "outputs/invalid.mp4", "video/mp4")
            session.add(DownloadArtifact(id=artifact_id, attempt_id=attempt.id, object_key=key))
            await session.commit()
        path = f"/api/v1/downloads/{artifact_id}/thumbnail"
        assert (await client.get(path, headers=user_headers())).status_code == 503
        await app.state.object_store.delete(key)
        assert (await client.get(path, headers=user_headers())).status_code == 410


@pytest.mark.asyncio
async def test_deep_video_can_prepare_another_task_after_completion(tmp_path: Path) -> None:
    async with running_app(tmp_path) as (client, app):
        task = await create_draft_task(client, "deep-remix")
        for phase, expected in [
            (ParentTaskPhase.GENERATING, False),
            (ParentTaskPhase.DELIVERABLE, True),
            (ParentTaskPhase.ACTION_REQUIRED, True),
        ]:
            async with app.state.database.session_factory() as session:
                stored = await session.get(ParentTask, uuid.UUID(task["id"]))
                stored.workflow_kind = "DEEP_VIDEO"
                stored.phase = phase
                await session.commit()
            response = await client.get(f"/api/v1/tasks/{task['id']}", headers=user_headers())
            assert response.json()["actionCapabilities"]["canRegenerate"] is expected
