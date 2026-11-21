import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from libtv_agent.adapters.libtv import LibTVAdapter, LibTVError


def _hidden_process_creation_flags() -> int:
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


@dataclass(frozen=True)
class TechnicalValidation:
    passed: bool
    issue_codes: tuple[str, ...]
    duration_seconds: float
    width: int
    height: int
    has_audio: bool
    sha256: str
    size_bytes: int


class TechnicalValidationOptions(TypedDict):
    duration_min: float
    duration_max: float
    target_ratio: float
    allow_audio: bool
    enforce_specs: bool


def technical_validation_options(
    generation_config: Mapping[str, object],
) -> TechnicalValidationOptions:
    expected_duration = float(
        generation_config.get("duration") or generation_config.get("durationSeconds") or 10
    )
    target_ratio = {
        "16:9": 16 / 9,
        "9:16": 9 / 16,
        "1:1": 1.0,
    }.get(str(generation_config.get("aspectRatio") or "9:16"), 9 / 16)
    # The editor owns audio handling: an upstream audio track must never make
    # an otherwise valid generated video ineligible for CUT.
    if generation_config.get("deepVideoMode") == "depth":
        width = float(generation_config.get("sourceWidth") or 0)
        height = float(generation_config.get("sourceHeight") or 0)
        if width > 0 and height > 0:
            target_ratio = width / height
        expected_duration = float(
            generation_config.get("sourceDurationSeconds") or expected_duration
        )
    return {
        "duration_min": max(0.1, expected_duration - 1),
        "duration_max": expected_duration + 1,
        "target_ratio": target_ratio,
        "allow_audio": True,
        # The generation side verifies a readable file, not creative output specs.
        # The CUT side decides whether the footage needs editing or reformatting.
        "enforce_specs": False,
    }


async def download_with_retries(
    adapter: LibTVAdapter, *, url: str, destination: Path, attempts: int = 5
) -> Path:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return await adapter.download_media(url=url, destination=destination)
        except LibTVError as exc:
            last_error = exc
            if exc.code != "DOWNLOAD_RETRYABLE":
                raise
        except Exception as exc:
            last_error = exc
            if not bool(getattr(exc, "retryable", False)):
                raise
    raise LibTVError(
        "DOWNLOAD_RETRYABLE", f"download failed after {attempts} attempts: {last_error}"
    )


async def technical_validate(
    path: Path,
    *,
    duration_min: float = 9.0,
    duration_max: float = 11.0,
    target_ratio: float = 9 / 16,
    ratio_tolerance: float = 0.01,
    allow_audio: bool = False,
    enforce_specs: bool = True,
    probe_timeout_seconds: float = 30,
) -> TechnicalValidation:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required for technical validation")
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_hidden_process_creation_flags(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), probe_timeout_seconds)
    except (TimeoutError, asyncio.CancelledError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        raise
    if process.returncode != 0:
        raise ValueError(f"video is not decodable: {stderr.decode('utf-8', errors='replace')}")
    metadata = json.loads(stdout)
    video_streams = [
        stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise ValueError("video stream is missing")
    video = video_streams[0]
    duration = float(metadata.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    has_audio = any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", []))
    issues: list[str] = []
    if width <= 0 or height <= 0:
        issues.append("INVALID_VIDEO_DIMENSIONS")
    if duration <= 0:
        issues.append("INVALID_VIDEO_DURATION")
    if enforce_specs:
        if not duration_min <= duration <= duration_max:
            issues.append("DURATION_OUT_OF_RANGE")
        if height > 0 and abs((width / height) - target_ratio) > ratio_tolerance:
            issues.append("ASPECT_RATIO_INVALID")
    # Keep provider audio for downstream editing; audio is never a validation error.
    digest, size = await asyncio.to_thread(_hash_and_size, path)
    return TechnicalValidation(
        not issues, tuple(issues), duration, width, height, has_audio, digest, size
    )


def _hash_and_size(path: Path, chunk_size: int = 4 * 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
