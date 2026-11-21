"""Provider-native Kling smart multi-shot forwarding tests (no paid calls)."""
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


def _identity(*, declared: bool = True, allowed: list[str] | None = None) -> dict[str, Any]:
    arguments: list[dict[str, Any]] = [
        {"name": "duration", "allowedValues": ["5", "10"]},
        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
        {"name": "enable_audio", "allowedValues": ["true", "false"]},
    ]
    if declared:
        arguments.append({
            "name": "prefer_multi_shots",
            "allowedValues": allowed if allowed is not None else ["true", "false"],
        })
    return {
        "user": {"accountType": "VIP"},
        "availableModels": {
            "omni_ref_video": {"models": [{
                "model": "kling-video-v3_0_omni",
                "arguments": arguments,
                "inputs": [{"name": "image_1"}, {"name": "image_2"}],
            }]}
        },
    }


def _capture_runner(calls: list[list[str]], identity: dict[str, Any]):
    async def runner(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        assert deadline_seconds > 0
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "expected"), [
    (True, "true"),
    (False, "false"),
    (None, None),
])
async def test_omni_multishot_explicitly_forwards_request(
    tmp_path: Path, enabled: bool | None, expected: str | None,
) -> None:
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"test")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path / "runner"),
        process_runner=_capture_runner(calls, _identity()),
    )
    result = await adapter.submit(VideoGenerationRequest(
        prompt="Preserve original sweater. Cut from medium shot to close-up.",
        model="kling-video-v3_0_omni",
        image_paths=(scene,),
        duration=5,
        aspect_ratio="9:16",
        multi_shot=enabled,
    ))
    assert result.provider_task_id == "generated-once"
    submission = next(item for item in calls if "omni_ref_video" in item)
    assert submission[submission.index("--enable_audio") + 1] == "false"
    if expected is None:
        assert "--prefer_multi_shots" not in submission
    else:
        assert submission[submission.index("--prefer_multi_shots") + 1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("declared", "allowed", "code"), [
    (False, None, "KLING_MULTI_SHOT_UNSUPPORTED"),
    (True, ["true"], "KLING_MULTI_SHOT_UNSUPPORTED"),
])
async def test_omni_multishot_rejects_unsupported_setting_before_paid_submit(
    tmp_path: Path, declared: bool, allowed: list[str] | None, code: str,
) -> None:
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"test")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path / "runner"),
        process_runner=_capture_runner(calls, _identity(declared=declared, allowed=allowed)),
    )
    with pytest.raises(KlingCliError) as error:
        await adapter.submit(VideoGenerationRequest(
            prompt="A natural step, then a sharp cut to the sweater.",
            model="kling-video-v3_0_omni",
            image_paths=(scene,),
            duration=5,
            aspect_ratio="9:16",
            multi_shot=False if declared else True,
        ))
    assert error.value.code == code
    assert not any("omni_ref_video" in call for call in calls)
