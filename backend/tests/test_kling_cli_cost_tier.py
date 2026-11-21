"""Kling CLI cost-tier forwarding: resolution and audio must reach argv.

Both fields were previously dropped inside submit(), so an operator who chose
720p or native audio was silently billed at the model default. These tests pin
the argv contract so the switch cannot become decorative again.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
)
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings


def _identity(*, resolution: list[str] | None = None) -> dict[str, Any]:
    arguments: list[dict[str, Any]] = [
        {"name": "duration", "allowedValues": ["5", "10"]},
        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
        {"name": "resolution", "allowedValues": resolution or ["720p", "1080p", "4k"]},
        {"name": "enable_audio", "allowedValues": ["true", "false"]},
        {"name": "keepOriginalSound", "allowedValues": ["true", "false"]},
        {"name": "prefer_multi_shots", "allowedValues": ["true", "false"]},
    ]
    return {
        "user": {"accountType": "VIP"},
        "availableModels": {
            "omni_ref_video": {"models": [{
                "model": "kling-video-v3_0_omni",
                "arguments": arguments,
                "inputs": [{"name": "image_1"}],
            }]}
        },
    }


def _capture_runner(calls: list[list[str]], identity: dict[str, Any]):
    async def runner(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        calls.append(arguments)
        if arguments[0] == "--version":
            return CliProcessResult(0, "0.2.3", "")
        command = arguments[5]
        if command == "who_am_i":
            body = identity
        elif command == "tool_list":
            body = {"tools": [
                {"name": "who_am_i", "description": "", "inputSchema": {}},
                {"name": "omni_ref_video", "description": "", "inputSchema": {}},
            ]}
        elif command == "omni_ref_video":
            body = {"generationId": "generated-once", "status": "submitted", "creditsConsumed": 60}
        else:
            return CliProcessResult(2, "", "unsupported fake command")
        return CliProcessResult(0, json.dumps({"ok": True, "body": body}), "")
    return runner


async def _submit(
    tmp_path: Path, identity: dict[str, Any], request: VideoGenerationRequest,
) -> list[str]:
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"test")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path / "runner"),
        process_runner=_capture_runner(calls, identity),
    )
    await adapter.submit(request)
    return next(item for item in calls if "omni_ref_video" in item)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["720p", "1080p"])
async def test_resolution_reaches_argv(tmp_path: Path, resolution: str) -> None:
    """720p is the cheap tier; dropping it billed every job at 1080p."""
    submission = await _submit(
        tmp_path,
        _identity(),
        VideoGenerationRequest(
            prompt="Wearer lifts the hem.",
            model="kling-video-v3_0_omni",
            image_paths=(tmp_path / "scene.png",),
            duration=10,
            aspect_ratio="9:16",
            resolution=resolution,
        ),
    )
    assert submission[submission.index("--resolution") + 1] == resolution


@pytest.mark.asyncio
async def test_resolution_not_declared_is_not_forced(tmp_path: Path) -> None:
    """Never invent a flag the model does not declare."""
    identity = _identity()
    model = identity["availableModels"]["omni_ref_video"]["models"][0]
    model["arguments"] = [
        item for item in model["arguments"] if item["name"] != "resolution"
    ]
    submission = await _submit(
        tmp_path,
        identity,
        VideoGenerationRequest(
            prompt="Wearer lifts the hem.",
            model="kling-video-v3_0_omni",
            image_paths=(tmp_path / "scene.png",),
            duration=10,
            aspect_ratio="9:16",
            resolution="720p",
        ),
    )
    assert "--resolution" not in submission


@pytest.mark.asyncio
async def test_undeclared_resolution_value_is_rejected(tmp_path: Path) -> None:
    """An unsupported tier must fail before it becomes a paid job."""
    with pytest.raises(KlingCliError) as excinfo:
        await _submit(
            tmp_path,
            _identity(resolution=["720p"]),
            VideoGenerationRequest(
                prompt="Wearer lifts the hem.",
                model="kling-video-v3_0_omni",
                image_paths=(tmp_path / "scene.png",),
                duration=10,
                aspect_ratio="9:16",
                resolution="1080p",
            ),
        )
    assert excinfo.value.code == "KLING_RESOLUTION_UNSUPPORTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(("audio", "expected"), [
    ("native", "true"),
    ("off", "false"),
    (None, "false"),
])
async def test_audio_switch_reaches_argv(
    tmp_path: Path, audio: str | None, expected: str,
) -> None:
    """Omni defaults enable_audio to true; off/original must not inherit it."""
    submission = await _submit(
        tmp_path,
        _identity(),
        VideoGenerationRequest(
            prompt="Wearer lifts the hem.",
            model="kling-video-v3_0_omni",
            image_paths=(tmp_path / "scene.png",),
            duration=10,
            aspect_ratio="9:16",
            audio=audio,
        ),
    )
    assert submission[submission.index("--enable_audio") + 1] == expected


@pytest.mark.asyncio
async def test_original_audio_keeps_source_sound(tmp_path: Path) -> None:
    """"original" means keep the source soundtrack, not mute everything."""
    submission = await _submit(
        tmp_path,
        _identity(),
        VideoGenerationRequest(
            prompt="Wearer lifts the hem.",
            model="kling-video-v3_0_omni",
            image_paths=(tmp_path / "scene.png",),
            duration=10,
            aspect_ratio="9:16",
            audio="original",
        ),
    )
    assert submission[submission.index("--enable_audio") + 1] == "false"
    assert submission[submission.index("--keepOriginalSound") + 1] == "true"
