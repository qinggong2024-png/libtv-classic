import asyncio
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.domain.models import (
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationVariant,
    ParentTask,
)
from libtv_agent.services.deep_video_media import media_command
from libtv_agent.services.media_download_metrics import ORIGINAL_DOWNLOAD, record_download
from sqlalchemy import select

router = APIRouter(prefix="/downloads", tags=["downloads"])


@dataclass(frozen=True)
class StoredVideo:
    id: uuid.UUID
    object_key: str | None
    depth: bool = False


async def _owned_artifact(
    artifact_id: uuid.UUID, actor: Actor, session: Session
) -> tuple[StoredVideo, GenerationVariant, ParentTask]:
    row = await session.execute(
        select(DownloadArtifact, GenerationVariant, ParentTask)
        .join(GenerationAttempt, GenerationAttempt.id == DownloadArtifact.attempt_id)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(DownloadArtifact.id == artifact_id, ParentTask.user_id == actor.id)
    )
    result = row.first()
    if result is not None:
        artifact, variant, task = result
        return StoredVideo(artifact.id, artifact.object_key), variant, task
    # Depth intermediates remain separate from final artifacts and quality gates.
    # Resolve historical saved files without modifying tasks or rerunning providers.
    row = await session.execute(
        select(GenerationCommand, GenerationVariant, ParentTask)
        .join(GenerationAttempt, GenerationAttempt.id == GenerationCommand.attempt_id)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(
            GenerationCommand.id == artifact_id,
            ParentTask.user_id == actor.id,
            ParentTask.workflow_kind == "DEEP_VIDEO",
        )
    )
    result = row.first()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "download artifact was not found")
    command, variant, task = result
    key = (command.command_manifest_json or {}).get("depthPipeline", {}).get("depthObjectKey")
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "depth video was not found")
    return StoredVideo(command.id, key, depth=True), variant, task


@router.get("/{artifact_id}/url")
async def signed_download_url(
    artifact_id: uuid.UUID, actor: Actor, session: Session, request: Request
) -> dict[str, object]:
    artifact, _, _ = await _owned_artifact(artifact_id, actor, session)
    if not artifact.object_key:
        raise HTTPException(status.HTTP_410_GONE, "server copy has expired; use remote restore")
    expires_seconds = 300
    signed = await request.app.state.object_store.presign_get(
        artifact.object_key, expires_seconds=expires_seconds
    )
    return {
        "url": signed or f"/api/v1/downloads/{artifact.id}",
        "expiresInSeconds": expires_seconds,
        "directObjectStore": signed is not None,
    }


@router.get("/{artifact_id}")
async def download_artifact(
    artifact_id: uuid.UUID, actor: Actor, session: Session, request: Request,
    download: bool = False,
) -> FileResponse:
    artifact, variant, task = await _owned_artifact(artifact_id, actor, session)
    if not artifact.object_key:
        raise HTTPException(status.HTTP_410_GONE, "server copy has expired; use remote restore")
    try:
        local = await request.app.state.object_store.localize(
            artifact.object_key,
            request.app.state.settings.artifact_root / "cache" / "downloads" / f"{artifact.id}.mp4",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status.HTTP_410_GONE, "server copy has expired; use remote restore"
        ) from exc
    suffix = "depth" if artifact.depth else variant.variant_type.value.lower()
    filename = f"{task.title}-{suffix}.mp4"
    if download and not artifact.depth:
        user_id, media_id = actor.id, str(artifact.id)
        await session.commit()  # release read transaction before optional metadata
        async with request.app.state.database.download_metadata_session_factory() as metadata:
            await record_download(
                metadata, user_id=user_id, action=ORIGINAL_DOWNLOAD, media_id=media_id,
            )
    return FileResponse(
        local,
        media_type="video/mp4",
        filename=filename,
    )


@router.get("/{artifact_id}/thumbnail")
async def artifact_thumbnail(
    artifact_id: uuid.UUID, actor: Actor, session: Session, request: Request
) -> FileResponse:
    artifact, _, _ = await _owned_artifact(artifact_id, actor, session)
    if not artifact.object_key:
        raise HTTPException(status.HTTP_410_GONE, "server copy has expired; use remote restore")
    cache = request.app.state.settings.artifact_root / "cache"
    thumbnail = cache / "video-thumbnails" / f"{artifact.id}.jpg"
    if not await asyncio.to_thread(thumbnail.is_file):
        try:
            local = await request.app.state.object_store.localize(
                artifact.object_key, cache / "downloads" / f"{artifact.id}.mp4"
            )
        except FileNotFoundError as exc:
            raise HTTPException(status.HTTP_410_GONE, "server copy has expired") from exc
        try:
            data = await media_command(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(local),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(480,iw)':'min(480,ih)':force_original_aspect_ratio=decrease",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            )
            if not data:
                raise ValueError("empty video thumbnail")
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "video thumbnail unavailable"
            ) from exc
        await asyncio.to_thread(thumbnail.parent.mkdir, parents=True, exist_ok=True)
        temporary = thumbnail.with_name(f"{artifact.id}-{uuid.uuid4()}.tmp")
        try:
            await asyncio.to_thread(temporary.write_bytes, data)
            await asyncio.to_thread(temporary.replace, thumbnail)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
    return FileResponse(thumbnail, media_type="image/jpeg")
