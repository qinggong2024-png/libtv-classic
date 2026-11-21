import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.domain.models import StagedUpload
from libtv_agent.domain.schemas import UploadRead
from libtv_agent.services.deep_video_media import (
    MAX_VIDEO_BYTES,
    inspect_reference_video,
    media_command,
)
from libtv_agent.services.idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    load_idempotent_response,
    store_idempotent_response,
)

router = APIRouter(tags=["uploads"])


class VideoUploadRead(UploadRead):
    duration_seconds: float


@router.post("/uploads/video", response_model=VideoUploadRead, status_code=201)
async def upload_video(
    actor: Actor,
    session: Session,
    request: Request,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> VideoUploadRead:
    filename = Path(file.filename or "reference.mp4").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov"}:
        raise HTTPException(422, detail={"code": "INPUT_INVALID", "message": "仅支持 MP4、MOV"})
    identity = uuid.uuid4()
    staging = request.app.state.settings.artifact_root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    local = staging / f"{identity}{suffix}"
    thumbnail = staging / f"{identity}.jpg"
    stored_keys: list[str] = []
    committed = False
    try:
        size = 0
        digest = hashlib.sha256()
        with local.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(
                        413,
                        detail={
                            "code": "INPUT_TOO_LARGE",
                            "message": "视频不能超过 200 MB",
                        },
                    )
                digest.update(chunk)
                await asyncio.to_thread(output.write, chunk)
        payload = {"sha256": digest.hexdigest(), "filename": filename}
        try:
            cached = await load_idempotent_response(
                session,
                tenant_id=str(actor.id),
                operation_type="VIDEO_UPLOAD",
                key=idempotency_key,
                payload=payload,
            )
        except (IdempotencyConflictError, IdempotencyInProgressError) as exc:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"}) from exc
        if cached:
            return VideoUploadRead.model_validate(cached[1])
        try:
            metadata = await inspect_reference_video(local)
            await media_command(
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(local),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                str(thumbnail),
            )
        except (ValueError, TimeoutError) as exc:
            raise HTTPException(422, detail={"code": "INPUT_INVALID", "message": str(exc)}) from exc
        store = request.app.state.object_store
        mime_type = "video/mp4" if suffix == ".mp4" else "video/quicktime"
        object_key = await store.put_file(
            local,
            f"users/{actor.id}/inputs/{identity}/reference{suffix}",
            mime_type,
        )
        stored_keys.append(object_key)
        thumbnail_key = await store.put_file(
            thumbnail,
            f"users/{actor.id}/thumbnails/{identity}.jpg",
            "image/jpeg",
        )
        stored_keys.append(thumbnail_key)
        session.add(
            StagedUpload(
                id=identity,
                user_id=actor.id,
                original_filename=filename,
                sha256=digest.hexdigest(),
                mime_type=mime_type,
                size_bytes=size,
                width=metadata.width,
                height=metadata.height,
                object_key=object_key,
                thumbnail_key=thumbnail_key,
            )
        )
        result = VideoUploadRead(
            id=identity,
            original_filename=filename,
            sha256=digest.hexdigest(),
            width=metadata.width,
            height=metadata.height,
            mime_type=mime_type,
            size_bytes=size,
            thumbnail_url=f"/api/v1/uploads/{identity}/thumbnail",
            duration_seconds=metadata.duration,
        )
        await store_idempotent_response(
            session,
            tenant_id=str(actor.id),
            operation_type="VIDEO_UPLOAD",
            key=idempotency_key,
            payload=payload,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        committed = True
        return result
    finally:
        if not committed:
            await session.rollback()
            await asyncio.gather(
                *(request.app.state.object_store.delete(key) for key in stored_keys),
                return_exceptions=True,
            )
        local.unlink(missing_ok=True)
        thumbnail.unlink(missing_ok=True)
