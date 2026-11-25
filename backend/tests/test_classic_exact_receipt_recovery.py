from datetime import UTC, datetime, timedelta

import pytest
from libtv_agent.adapters.video_provider import ProviderSubmissionMatch
from libtv_agent.domain.enums import AttemptStatus, ChargeStatus, CommandStatus
from libtv_agent.domain.models import (
    AuditEvent,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
)
from libtv_agent.services.classic_exact_receipt_recovery import attach_verified_handle
from sqlalchemy import select
from test_kling_submission_reconciliation import _unknown_api_job
from test_kling_submission_reconciliation import lab as lab


@pytest.mark.parametrize(
    "mode", ["valid", "wrong_account", "wrong_version", "wrong_time", "duplicate"],
)
async def test_exact_recovery(lab, mode):
    factory, supervisor, adapter = lab
    attempt_id, command_id, _ = await _unknown_api_job(factory, supervisor, task_id=None)
    provider = supervisor.settings.kling_api_provider_name
    start = datetime.now(UTC) - timedelta(minutes=1)
    async with factory() as session:
        a = await session.get(GenerationAttempt, attempt_id)
        a.submission_started_at = start
        a.submission_attempt_count = 1
        version = a.state_version
        fingerprint = a.intent_hash
        session.add(GenerationSubmissionAttempt(
            attempt_id=a.id, sequence=1, provider=provider,
            idempotency_key="test-recovery-" + a.id.hex,
            input_fingerprint=fingerprint, status="STARTED", started_at=start,
        ))
        await session.commit()
    if mode == "duplicate":
        await _unknown_api_job(factory, supervisor, task_id="cloud-recovered")
    match = ProviderSubmissionMatch(
        "cloud-recovered", "different" if mode == "wrong_account" else "api-account",
        fingerprint, start - timedelta(seconds=6 if mode == "wrong_time" else 1),
    )
    async def apply():
        await attach_verified_handle(
            factory, attempt_id, version + (mode == "wrong_version"), provider,
            match, "cloud-recovered", request_timeout_seconds=30,
        )
    if mode == "valid":
        await apply()
        with pytest.raises(ValueError):
            await apply()
    else:
        with pytest.raises(ValueError):
            await apply()
    async with factory() as session:
        a = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        receipt = await session.scalar(select(GenerationSubmissionAttempt).where(
            GenerationSubmissionAttempt.attempt_id == attempt_id
        ))
        audit = await session.scalar(select(AuditEvent).where(
            AuditEvent.action == "CLASSIC_ATTACH_VERIFIED_PROVIDER_HANDLE"
        ))
        assert a.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        assert a.charge_status == ChargeStatus.UNKNOWN
        assert command.status == CommandStatus.UNCERTAIN
        assert a.task_id == ("cloud-recovered" if mode == "valid" else None)
        assert receipt.status == ("ACCEPTED" if mode == "valid" else "STARTED")
        assert bool(audit) == (mode == "valid")
    adapter.submit.assert_not_awaited()
