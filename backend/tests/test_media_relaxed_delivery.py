"""Kling originals may differ from requested creative specs without blocking CUT."""
import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from libtv_agent.services.media import technical_validate, technical_validation_options


@pytest.mark.asyncio
async def test_original_video_specs_do_not_block_delivery(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required for end-to-end media check")
    source = tmp_path / "different-spec-with-audio.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:d=2:r=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
        capture_output=True,
    )
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    options = technical_validation_options({
        "durationSeconds": 15, "aspectRatio": "9:16", "audio": "off",
    })
    assert options["enforce_specs"] is False
    delivered = await technical_validate(source, **options)
    assert delivered.passed
    assert delivered.issue_codes == ()
    assert delivered.has_audio
    assert delivered.width == 160 and delivered.height == 90
    assert delivered.sha256 == original_hash

    strict = await technical_validate(
        source, duration_min=14, duration_max=16,
        target_ratio=9 / 16, enforce_specs=True,
    )
    assert not strict.passed
    assert set(strict.issue_codes) == {
        "DURATION_OUT_OF_RANGE", "ASPECT_RATIO_INVALID",
    }


@pytest.mark.asyncio
async def test_unreadable_file_is_still_not_deliverable(tmp_path: Path) -> None:
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe required")
    corrupt = tmp_path / "not-a-video.mp4"
    corrupt.write_bytes(b"not an mp4")
    with pytest.raises(ValueError, match="video is not decodable"):
        await technical_validate(corrupt, **technical_validation_options({}))
