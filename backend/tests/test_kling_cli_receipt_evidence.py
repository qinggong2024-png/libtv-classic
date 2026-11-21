"""Synthetic process output only: no provider calls or paid generation."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
)
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings
from test_kling_cli_adapter import _runner, _settings

ACCEPTED = json.dumps({"ok": True, "body": {"generationId": "cloud-receipt"}})
REJECTED = json.dumps({"ok": False, "status": 429, "body": {"message": "rate limit"}})


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        (ACCEPTED + "\n" + REJECTED, ""),
        (REJECTED, ACCEPTED),
        (ACCEPTED, REJECTED),
        (REJECTED, json.dumps({"accepted": True})),
        ("", ACCEPTED),
        (ACCEPTED + "\n" + json.dumps({"ok": True, "body": {}}), ""),
    ],
)
async def test_acceptance_in_either_stream_prevents_429_resubmit(
    tmp_path, exit_code, stdout, stderr
):
    base_runner = _runner([])
    submissions = 0

    async def run(arguments, deadline_seconds):
        nonlocal submissions
        if "image_to_video" in arguments:
            submissions += 1
            return CliProcessResult(exit_code, stdout, stderr)
        return await base_runner(arguments, deadline_seconds)

    image = tmp_path / "frame.png"
    image.write_bytes(b"synthetic")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=run)
    request = VideoGenerationRequest(prompt="unchanged", model=None, image_paths=(image,))
    if exit_code == 0 and stdout == ACCEPTED:
        assert (await adapter.submit(request)).provider_task_id == "cloud-receipt"
    else:
        with pytest.raises(KlingCliError) as failure:
            await adapter.submit(request)
        assert failure.value.submitted and failure.value.uncertain
        assert failure.value.details["verifiedRejected"] is False
        if ACCEPTED in stdout or ACCEPTED in stderr:
            assert failure.value.provider_task_id == "cloud-receipt"
    assert submissions == 1


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_conflicting_handles_are_never_resolved_by_stream_preference(tmp_path, exit_code):
    other = json.dumps({"generationId": "other-task"})
    runner = AsyncMock(return_value=CliProcessResult(exit_code, ACCEPTED, other))
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, runner_root=tmp_path), process_runner=runner
    )
    with pytest.raises(KlingCliError) as failure:
        await adapter._invoke("image_to_video", submitted=True)
    assert failure.value.code == "KLING_SUBMIT_RESULT_CONFLICT"
    assert failure.value.uncertain
    assert failure.value.provider_task_id is None
    assert runner.await_count == 1


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("accepted", [False, True])
async def test_credit_rejection_preserves_existing_contract_unless_receipt_conflicts(
    tmp_path, exit_code, accepted
):
    output = json.dumps({"ok": False, "body": {"message": "insufficient credits"}})
    runner = AsyncMock(
        return_value=CliProcessResult(exit_code, output, ACCEPTED if accepted else "")
    )
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, runner_root=tmp_path), process_runner=runner
    )
    with pytest.raises(KlingCliError) as failure:
        await adapter._invoke("image_to_video", submitted=True)
    assert failure.value.code == "KLING_INSUFFICIENT_CREDITS"
    assert failure.value.submitted is accepted
    assert failure.value.uncertain is accepted
    assert failure.value.provider_task_id == ("cloud-receipt" if accepted else None)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_timeout_drains_original_communication_without_losing_receipt(
    tmp_path, monkeypatch, stream
):
    killed = asyncio.Event()

    async def communicate():
        await killed.wait()
        data = ACCEPTED.encode()
        return (data, b"") if stream == "stdout" else (b"", data)

    communication = AsyncMock(side_effect=communicate)
    child = SimpleNamespace(communicate=communication, kill=killed.set)
    monkeypatch.setattr(
        "libtv_agent.adapters.kling_official_cli.asyncio.create_subprocess_exec",
        AsyncMock(return_value=child),
    )
    adapter = KlingOfficialCliAdapter(Settings(_env_file=None, runner_root=tmp_path))
    with pytest.raises(KlingCliError) as failure:
        await adapter._run_process(["synthetic-command"], 0.01)
    assert failure.value.code == "KLING_CLI_TIMEOUT"
    assert failure.value.provider_task_id == "cloud-receipt"
    assert failure.value.uncertain
    communication.assert_awaited_once()


async def test_read_only_timeout_does_not_claim_paid_submission(tmp_path):
    runner = AsyncMock(side_effect=KlingCliError(
        "KLING_CLI_TIMEOUT", "read-only timeout", uncertain=True
    ))
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, runner_root=tmp_path), process_runner=runner
    )
    with pytest.raises(KlingCliError) as failure:
        await adapter._invoke("who_am_i")
    assert not failure.value.submitted and not failure.value.uncertain


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_real_local_child_keeps_already_written_handle_on_timeout(tmp_path, stream):
    # Launch Python, NOT Kling. This exercises actual pipe buffering and kill/drain.
    adapter = KlingOfficialCliAdapter(Settings(
        _env_file=None, runner_root=tmp_path, kling_cli_executable=sys._base_executable
    ))
    code = (
        f"import sys,time; print({ACCEPTED!r}, file=sys.{stream}, flush=True); time.sleep(30)"
    )
    with pytest.raises(KlingCliError) as failure:
        await adapter._run_process(["-c", code], 1.0)
    assert failure.value.code == "KLING_CLI_TIMEOUT"
    assert failure.value.provider_task_id == "cloud-receipt"
