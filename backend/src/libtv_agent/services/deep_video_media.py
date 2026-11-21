"""Local validation shared by upload, preflight and dispatch; never contacts a provider."""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from libtv_agent.services.media import _hidden_process_creation_flags

MAX_VIDEO_BYTES = 200 * 1024 * 1024
DEPTH_MODEL = "wavespeed-ai/depth-anything/video"
RECREATE_MODEL = "bytedance/seedance-2.0/video-edit"
FLASH_RECREATE_MODEL = "bytedance/seedance-2.5-flash/video-edit"
LEGACY_RECREATE_MODEL = "wavespeed-ai/wan-2.1-14b-vace"
DEEP_VIDEO_MODELS = frozenset({DEPTH_MODEL, RECREATE_MODEL, LEGACY_RECREATE_MODEL})
RECREATE_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*/video-edit$"


def is_recreate_video_model(model: str | None) -> bool:
    return bool(model and re.fullmatch(RECREATE_MODEL_PATTERN, model))


def is_deep_video_model(model: str | None) -> bool:
    return model in DEEP_VIDEO_MODELS or is_recreate_video_model(model)


@dataclass(frozen=True)
class VideoMetadata:
    duration: float
    width: int
    height: int


async def media_command(*arguments: str) -> bytes:
    executable = shutil.which(arguments[0])
    if not executable:
        raise ValueError(f"服务器缺少 {arguments[0]}，无法验证视频")
    process = await asyncio.create_subprocess_exec(
        executable,
        *arguments[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_hidden_process_creation_flags(),
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise ValueError("视频无法解码，请上传有效的 MP4 或 MOV 文件")
    return stdout


async def inspect_reference_video(path: Path) -> VideoMetadata:
    if path.suffix.lower() not in {".mp4", ".mov"}:
        raise ValueError("参考视频仅支持 MP4、MOV")
    valid_size = await asyncio.to_thread(
        lambda: path.is_file() and 0 < path.stat().st_size <= MAX_VIDEO_BYTES
    )
    if not valid_size:
        raise ValueError("参考视频不能为空或超过 200 MB")
    raw = await media_command(
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    )
    metadata = json.loads(raw)
    streams = [s for s in metadata.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise ValueError("文件中没有可用的视频画面")
    video = streams[0]
    duration = float(metadata.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if not math.isfinite(duration) or not 0 < duration <= 600 or min(width, height) <= 0:
        raise ValueError("参考视频时长须大于 0 且不超过 600 秒，并具有有效尺寸")
    return VideoMetadata(duration, width, height)


async def prepare_flash_edit_input(path: Path, destination: Path, aspect_ratio: str) -> None:
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    # Flash derives the canvas from the source. Pad to preserve the depth map's
    # framing and motion when the requested canvas has a different aspect ratio.
    filters = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2:reset_sar=1,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    await media_command(
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(destination),
    )
