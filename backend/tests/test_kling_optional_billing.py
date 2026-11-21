"""Billing metadata must not hide an accepted task or its generation result."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from libtv_agent.adapters.kling_official_api import (
    ApiHttpResponse,
    KlingApiError,
    KlingOfficialApiAdapter,
    _parse_billing,
)
from libtv_agent.config import Settings
from libtv_agent.domain.enums import AttemptStatus, CommandStatus, GenerationStatus
from libtv_agent.domain.models import (
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    GenerationVariant,
)
from sqlalchemy import select
from test_kling_migrated_submission import install_submission_migration
from test_kling_submission_reconciliation import lab, ready  # noqa: F401

INVALID_BILLING = [
    [{"charge_type": "future_type", "amount": "8"}],
    [{"charge_type": ["unit"], "amount": "8"}],
    [{"charge_type": "unit", "amount": "invalid"}],
    [{"charge_type": "unit", "amount": "NaN"}],
    [{"charge_type": "unit"}],
    [None],
    {"charge_type": "unit", "amount": "8"},
    [
        {"charge_type": "unit", "amount": "8"},
        {"charge_type": "future_type", "amount": "2"},
    ],
]


def _adapter(tmp_path, **kwargs):
    return KlingOfficialApiAdapter(
        Settings(
            app_env="test", runner_root=tmp_path, kling_api_key="test-key",
            _env_file=None,
        ),
        **kwargs,
    )


@pytest.mark.parametrize("billing", INVALID_BILLING)
@pytest.mark.parametrize("status", ["submitted", "processing", "succeeded", "failed"])
async def test_existing_task_remains_queryable_with_invalid_billing(tmp_path, billing, status):
    data = {
        "id": "existing-paid-task", "status": status, "billing": billing,
        "outputs": [{"type": "video", "url": "https://cdn.example/result.mp4"}],
    }
    calls = []

    async def respond(method, url, headers, body, request_timeout):
        calls.append((method, url, body))
        return ApiHttpResponse(200, {}, json.dumps({"code": 0, "data": [data]}).encode())

    result = await _adapter(tmp_path, http_request=respond).query("existing-paid-task")

    assert result.status == {"submitted": "queued", "processing": "running"}.get(status, status)
    assert result.outputs[0].url == data["outputs"][0]["url"]
    assert result.raw_response == data
    assert result.billing == ()
    assert result.credits_consumed is None  # Unknown billing is never a zero charge.
    assert len(calls) == 1 and calls[0][0] == "GET" and calls[0][2] is None


@pytest.mark.parametrize("billing", INVALID_BILLING)
def test_accepted_task_handle_survives_invalid_optional_billing(tmp_path, billing):
    data = {"id": "accepted-task", "status": "submitted", "billing": billing}
    result = _adapter(tmp_path)._submission_result(data)
    assert result.provider_task_id == "accepted-task"
    assert result.raw_response == data
    assert result.billing == ()
    assert result.credits_consumed is None


def test_billing_report_parser_stays_strict_and_preserves_valid_decimal_receipts():
    with pytest.raises(KlingApiError):
        _parse_billing({"billing": INVALID_BILLING[0]})
    charges = _parse_billing({"billing": [{"charge_type": "unit", "amount": "1.5"}]})
    assert charges[0].amount == Decimal("1.5")


@pytest.mark.parametrize("billing", INVALID_BILLING)
@pytest.mark.parametrize("status", ["submitted", "processing"])
async def test_migrated_runner_does_not_exhaust_recovery_for_optional_billing(
    lab, billing, status,  # noqa: F811
):
    factory, supervisor, _ = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    settings = supervisor.settings
    settings.kling_api_enabled = True
    settings.kling_api_key = "synthetic-billing-test-key"
    provider = settings.kling_api_provider_name
    calls = []

    async def respond(method, url, headers, body, request_timeout):
        assert method == "GET" and body is None  # Never create a new paid task.
        calls.append(url)
        return ApiHttpResponse(200, {}, json.dumps({"code": 0, "data": [{
            "id": "original-paid-handle", "status": status, "billing": billing,
        }]}).encode())

    supervisor._kling_adapters[provider] = KlingOfficialApiAdapter(
        settings, http_request=respond,
    )
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        attempt.provider = provider
        attempt.task_id = "original-paid-handle"
        attempt.status = AttemptStatus.RUNNING
        command = await session.get(GenerationCommand, command_id)
        command.status = CommandStatus.PROCESS_STARTED
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=provider,
            idempotency_key="original-paid-submission", input_fingerprint=attempt.intent_hash,
            status="ACCEPTED", started_at=datetime.now(UTC),
            provider_task_id="original-paid-handle",
        ))
        await session.commit()

    # The original defect stopped after five observations. Exercise six complete
    # runner/database cycles against the real migrated submission table.
    for _ in range(6):
        async with factory() as session:
            attempt = await session.get(GenerationAttempt, attempt_id)
            attempt.last_polled_at = None
            await session.commit()
            assert await supervisor.poll_kling_tasks(session) == 1

    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        variant = await session.get(GenerationVariant, attempt.variant_id)
        receipts = list(await session.scalars(select(GenerationSubmissionAttempt)))
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == "original-paid-handle"
        assert attempt.reconciliation_attempt_count == 0
        assert attempt.last_error_code is None
        assert attempt.credits_consumed is None
        assert attempt.raw_response_json["billing"] == billing
        assert variant.generation_status == GenerationStatus.RUNNING
        assert len(receipts) == 1 and receipts[0].status == "ACCEPTED"
    assert len(calls) == 6
