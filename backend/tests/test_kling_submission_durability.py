"""No network: test exact receipts, upload boundaries and safe restart."""

import json
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
)
from libtv_agent.adapters.kling_submission_receipts import KlingSubmissionReceipt
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from test_kling_cli_adapter import _runner, _settings


def receipt(tmp_path, **updates):
    return KlingSubmissionReceipt(
        tmp_path, updates.get("operation_key", "attempt:generation"), "kling_official_cli",
        updates.get("account_id", "account-a"), updates.get("fingerprint", "input-a"),
    )


async def test_upload_failure_is_not_an_unknown_paid_video_and_later_task_can_run(tmp_path):
    calls = []
    base = _runner(calls)
    upload_fails = True

    async def run(arguments, deadline):
        if "file_upload" in arguments and upload_fails:
            calls.append(arguments)
            return CliProcessResult(1, "", "rate limit")
        return await base(arguments, deadline)

    image = tmp_path / "image.png"
    image.write_bytes(b"reference")
    request = VideoGenerationRequest("original prompt", None, image_paths=(image,))
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=run)
    with pytest.raises(KlingCliError) as error:
        await adapter.submit(request)
    assert error.value.code == "KLING_UPLOAD_FAILED"
    assert not error.value.submitted and not error.value.uncertain
    assert error.value.details["generationInvoked"] is False
    assert not any("image_to_video" in call for call in calls)
    upload_fails = False
    assert (await adapter.submit(request)).provider_task_id == "generation-123"
    assert sum("image_to_video" in call for call in calls) == 1


async def test_uploads_are_serial_and_preserve_exact_reference_order(tmp_path):
    import asyncio

    calls = []
    active = 0
    base = _runner(calls)

    async def run(arguments, deadline):
        nonlocal active
        if "file_upload" in arguments:
            active += 1
            assert active == 1
            await asyncio.sleep(0)
            result = await base(arguments, deadline)
            active -= 1
            return result
        assert active == 0
        return await base(arguments, deadline)

    paths = tuple(tmp_path / name for name in ("first.png", "last.png"))
    for path in paths:
        path.write_bytes(path.name.encode())
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=run)
    await adapter.submit(VideoGenerationRequest("unchanged", None, image_paths=paths))
    paid = next(call for call in calls if "image_to_video" in call)
    assert paid[paid.index("--image") + 1] == "https://uploads.invalid/first.png"
    assert paid[paid.index("--tailImage") + 1] == "https://uploads.invalid/last.png"
    assert paid[-1] == "unchanged"


@pytest.mark.parametrize("message", [
    "MCP tool call failed (who_am_i): rate limit",
    "MCP 工具调用失败 (who_am_i)：rate limit",
])
async def test_cli_capability_lookup_failure_is_before_paid_tool(tmp_path, message):
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path),
        process_runner=AsyncMock(return_value=CliProcessResult(1, "", message)),
    )
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True)
    assert not error.value.submitted and not error.value.uncertain
    assert error.value.details["phase"] == "capability_lookup"


async def test_prior_acceptance_overrides_preflight_sounding_error(tmp_path):
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=AsyncMock(
        return_value=CliProcessResult(1, json.dumps({"generationId": "paid-id"}),
                                     "MCP tool call failed (who_am_i): rate limit"),
    ))
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True)
    assert error.value.submitted and error.value.uncertain
    assert error.value.provider_task_id == "paid-id"


async def test_exact_cli_receipt_is_durable_before_return_and_blocks_duplicate(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"reference")
    calls = []
    settings = _settings(tmp_path)
    adapter = KlingOfficialCliAdapter(settings, process_runner=_runner(calls))
    request = VideoGenerationRequest(
        "private prompt must never enter receipt", None, image_paths=(image,),
        submission_operation_key="attempt:generation", submission_account_id="account-a",
        submission_fingerprint="input-a",
    )
    stored = receipt(settings.runner_root)
    result = await adapter.submit(request)
    assert stored.load()["providerTaskId"] == result.provider_task_id
    assert stored.load()["creditsConsumed"] == 15
    assert request.prompt not in stored.path.read_text()
    restarted = KlingOfficialCliAdapter(settings, process_runner=_runner(calls))
    with pytest.raises(KlingCliError) as error:
        await restarted.submit(request)
    assert error.value.code == "KLING_SUBMISSION_RECOVERY_REQUIRED"
    assert error.value.provider_task_id == result.provider_task_id
    assert sum("image_to_video" in call for call in calls) == 1
    # Another stage must never inherit this stage's handle.
    assert receipt(settings.runner_root, operation_key="attempt:replacement").load() is None
    with pytest.raises(ValueError):
        receipt(settings.runner_root, account_id="different-account").load()
    with pytest.raises(ValueError):
        receipt(settings.runner_root, fingerprint="different-input").load()


@pytest.mark.parametrize("status", ["STARTED", "UNCERTAIN"])
async def test_existing_unknown_receipt_never_allows_a_second_paid_invocation(tmp_path, status):
    stored = receipt(tmp_path)
    stored.begin()
    if status != "STARTED":
        stored.finish(status)
    runner = AsyncMock()
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=runner)
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert error.value.uncertain
    runner.assert_not_awaited()


async def test_receipt_disk_failure_prevents_paid_call(tmp_path, monkeypatch):
    stored = receipt(tmp_path)
    runner = AsyncMock()
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=runner)
    monkeypatch.setattr(KlingSubmissionReceipt, "_write", staticmethod(
        lambda *_: (_ for _ in ()).throw(OSError("disk full"))
    ))
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert error.value.code == "KLING_SUBMISSION_RECORD_FAILED"
    assert not error.value.submitted
    runner.assert_not_awaited()


async def test_timeout_with_known_handle_is_journaled_for_restart(tmp_path):
    stored = receipt(tmp_path)
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=AsyncMock(
        side_effect=KlingCliError("KLING_CLI_TIMEOUT", "timeout",
                                  provider_task_id="accepted-before-timeout", uncertain=True),
    ))
    with pytest.raises(KlingCliError):
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert stored.load()["providerTaskId"] == "accepted-before-timeout"
    assert stored.load()["status"] == "UNCERTAIN"


async def test_verified_rejection_can_retry_but_uncertain_error_cannot(tmp_path):
    stored = receipt(tmp_path)
    runner = AsyncMock(side_effect=[
        CliProcessResult(1, json.dumps({"ok": False, "status": 429}), ""),
        CliProcessResult(0, json.dumps({"generationId": "accepted-once"}), ""),
    ])
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=runner)
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert error.value.details["verifiedRejected"]
    assert stored.load()["status"] == "REJECTED"
    await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert stored.load()["providerTaskId"] == "accepted-once"
    with pytest.raises(KlingCliError):
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert runner.await_count == 2


async def test_post_response_disk_failure_preserves_handle_in_error_for_db(tmp_path, monkeypatch):
    stored = receipt(tmp_path)
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=AsyncMock(
        return_value=CliProcessResult(0, json.dumps({"generationId": "accepted-id"}), ""),
    ))
    monkeypatch.setattr(KlingSubmissionReceipt, "finish",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert error.value.provider_task_id == "accepted-id"
    assert error.value.uncertain
    assert stored.load()["status"] == "STARTED"


async def test_corrupt_existing_receipt_stays_uncertain_and_never_resubmits(tmp_path):
    stored = receipt(tmp_path)
    stored.path.parent.mkdir(parents=True)
    stored.path.write_text("corrupted")
    runner = AsyncMock()
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=runner)
    with pytest.raises(KlingCliError) as error:
        await adapter._invoke("image_to_video", submitted=True, receipt=stored)
    assert error.value.uncertain
    runner.assert_not_awaited()
