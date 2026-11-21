"""Regression tests for WaveSpeed deep-video balance and two-stage recovery."""

import json
from types import SimpleNamespace

import pytest

from libtv_agent.adapters.wavespeed import (
    WaveSpeedAdapter, WaveSpeedHttpResponse, _error_for_response,
)
from libtv_agent.adapters.wavespeed_video import WaveSpeedVideoAdapter, WaveSpeedVideoError
from libtv_agent.domain.enums import AttemptStatus, ChargeStatus, CommandStatus, GenerationStatus
from libtv_agent.runner.deep_video_pipeline import queue_replacement, pipeline_state
from libtv_agent.adapters.video_provider import ProviderOutput, ProviderTaskResult
from test_wavespeed_adapter import _settings
from test_depth_first_pipeline import graph
from test_launch_started_recovery import _supervisor
from test_launch_started_recovery import session as session  # noqa: F401


def test_http400_insufficient_credits_is_a_definite_rejection():
    response = WaveSpeedHttpResponse(400, {}, b"")
    error = _error_for_response(
        response,
        {"error": "Insufficient credits. Please top up your account to continue."},
        submitted=True,
    )
    assert error.code == "WAVESPEED_BALANCE_INSUFFICIENT"
    assert error.submitted is False
    assert error.uncertain is False
    assert error.details["httpStatus"] == 400


@pytest.mark.asyncio
async def test_balance_quote_reads_exact_payload_without_submitting(tmp_path):
    seen = []
    async def transport(method, url, headers, body, timeout):
        del headers, timeout
        seen.append((method, url.rsplit("/", 1)[-1], json.loads(body) if body else None))
        if method == "GET" and url.endswith("/balance"):
            return WaveSpeedHttpResponse(200, {}, b'{"code":200,"data":{"balance":2.1421}}')
        if method == "POST" and url.endswith("/model/price"):
            return WaveSpeedHttpResponse(200, {}, b'{"code":200,"data":{"discounted_price":2.16,"currency":"USD"}}')
        raise AssertionError("A paid generation call must not occur")
    api = WaveSpeedAdapter(_settings(tmp_path), http_request=transport)
    video = WaveSpeedVideoAdapter(_settings(tmp_path), client=api)
    payload = {
        "video": "https://cdn.example/depth.mp4", "reference_images": [],
        "prompt": "retain the subject and motion", "duration": 8,
        "aspect_ratio": "9:16", "resolution": "720p",
    }
    with pytest.raises(WaveSpeedVideoError) as caught:
        await video._preflight_budget("bytedance/seedance-2.0/video-edit", payload)
    assert caught.value.code == "WAVESPEED_BALANCE_INSUFFICIENT"
    assert caught.value.details["shortfallUsd"] == 0.0179
    assert {item[1] for item in seen} == {"balance", "price"}
    quote = next(item[2] for item in seen if item[1] == "price")
    assert quote == {"model_id": "bytedance/seedance-2.0/video-edit", "input": payload}


@pytest.mark.asyncio
async def test_rejected_replacement_preserves_paid_depth_without_cloud_unknown(session, tmp_path):
    settings, task, variant, attempt, command = await graph(session, tmp_path)
    await queue_replacement(
        session, command, attempt, variant, task,
        ProviderTaskResult("succeeded", "completed",
                           (ProviderOutput("https://cdn.example/depth.mp4"),), {}),
    )
    supervisor = _supervisor(tmp_path)
    supervisor.settings = settings
    error = WaveSpeedVideoError(
        "WAVESPEED_BALANCE_INSUFFICIENT",
        "Insufficient credits",
        submitted=False, uncertain=False, retryable=False,
        details={"httpStatus": 400},
    )
    await supervisor._handle_error(session, command, attempt, error)
    assert pipeline_state(command)["depthTaskId"] == "depth-task"
    assert attempt.task_id is None
    assert attempt.status == AttemptStatus.FAILED_BEFORE_SUBMIT
    assert attempt.charge_status == ChargeStatus.UNKNOWN
    assert command.status == CommandStatus.TERMINAL
    assert variant.generation_status == GenerationStatus.FAILED
    assert attempt.last_error_code == "WAVESPEED_BALANCE_INSUFFICIENT"


@pytest.mark.asyncio
async def test_uncertain_replacement_keeps_unknown_after_paid_depth(session, tmp_path):
    from libtv_agent.adapters.kling_official_cli import KlingCliError
    settings, task, variant, attempt, command = await graph(session, tmp_path)
    await queue_replacement(
        session, command, attempt, variant, task,
        ProviderTaskResult("succeeded", "completed",
                           (ProviderOutput("https://cdn.example/depth.mp4"),), {}),
    )
    supervisor = _supervisor(tmp_path)
    supervisor.settings = settings
    await supervisor._handle_error(
        session, command, attempt,
        KlingCliError("WAVESPEED_SUBMISSION_UNCERTAIN", "network timeout",
                      submitted=True, uncertain=True),
    )
    assert attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
    assert attempt.charge_status == ChargeStatus.UNKNOWN
    assert variant.generation_status == GenerationStatus.UNKNOWN
