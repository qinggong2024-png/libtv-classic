"""Operator-only recovery of an evidenced missing official API handle.

Never submit, classify billing, download, or change the UNKNOWN status here.
The existing singleton Runner resumes its ordinary read-only polling path.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from libtv_agent.adapters.video_provider import ProviderSubmissionMatch
from libtv_agent.domain.enums import AttemptStatus, ChargeStatus, CommandStatus
from libtv_agent.domain.models import (
    AuditEvent,
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
)
from sqlalchemy import or_, select, text, update


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def attach_verified_handle(
    factory, attempt_id, expected_version, expected_provider, match, expected_cloud_id,
    *, request_timeout_seconds,
):
    if not isinstance(match, ProviderSubmissionMatch) or not (
        match.provider_task_id == expected_cloud_id
        and expected_cloud_id and match.account_id and match.input_fingerprint
        and match.submitted_at
    ):
        raise ValueError("exact receipt evidence required")
    async with factory() as session:
        try:
            # SQLite-only operator path: serialise the short CAS/duplicate checks.
            # All provider I/O must already be finished before this call.
            if session.get_bind().dialect.name != "sqlite":
                raise ValueError("this recovery procedure is for Classic SQLite only")
            await session.execute(text("PRAGMA busy_timeout=250"))
            await session.execute(text("BEGIN IMMEDIATE"))
            attempt = await session.get(GenerationAttempt, attempt_id)
            if not attempt or not (
                attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
                and attempt.charge_status == ChargeStatus.UNKNOWN
                and attempt.state_version == expected_version
                and not attempt.task_id
                and attempt.provider == expected_provider == "kling_official_api"
                and attempt.provider_account_id == match.account_id
                and attempt.intent_hash == match.input_fingerprint
                and attempt.submission_attempt_count == 1
                and attempt.submission_started_at
                and not attempt.local_output_path
            ):
                raise ValueError("attempt identity or state changed")
            start = utc(attempt.submission_started_at)
            end = min(
                utc(attempt.submission_finished_at) if attempt.submission_finished_at
                else start + timedelta(seconds=request_timeout_seconds + 30),
                start + timedelta(seconds=request_timeout_seconds + 30),
            )
            lower, upper = start - timedelta(seconds=5), end + timedelta(seconds=5)
            if not lower <= utc(match.submitted_at) <= upper:
                raise ValueError("submission time not within exact-match window")
            command = await session.scalar(select(GenerationCommand).where(
                GenerationCommand.attempt_id == attempt_id
            ))
            receipts = (await session.scalars(select(GenerationSubmissionAttempt).where(
                GenerationSubmissionAttempt.attempt_id == attempt_id
            ))).all()
            if not command or command.status != CommandStatus.UNCERTAIN or (
                (command.command_manifest_json or {}).get("depthPipeline")
            ):
                raise ValueError("unsupported command state or pipeline")
            if len(receipts) != 1:
                raise ValueError("ambiguous submit receipts")
            receipt = receipts[0]
            if not (
                receipt.sequence == 1 and receipt.provider == expected_provider
                and receipt.input_fingerprint == match.input_fingerprint
                and receipt.status in {"STARTED", "UNCERTAIN"} and not receipt.provider_task_id
            ):
                raise ValueError("original submit receipt changed")
            duplicate = await session.scalar(select(GenerationAttempt.id).where(
                GenerationAttempt.task_id == expected_cloud_id,
                GenerationAttempt.id != attempt_id,
            ).limit(1))
            duplicate_receipt = await session.scalar(select(GenerationSubmissionAttempt.id).where(
                GenerationSubmissionAttempt.provider_task_id == expected_cloud_id,
                GenerationSubmissionAttempt.id != receipt.id,
            ).limit(1))
            artifact = await session.scalar(select(DownloadArtifact.id).where(
                DownloadArtifact.attempt_id == attempt_id
            ).limit(1))
            if duplicate or duplicate_receipt or artifact:
                raise ValueError("provider handle or artifact already linked")
            changed = await session.execute(update(GenerationAttempt).where(
                GenerationAttempt.id == attempt_id,
                GenerationAttempt.state_version == expected_version,
                GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT,
                or_(GenerationAttempt.task_id.is_(None), GenerationAttempt.task_id == ""),
            ).values(
                task_id=expected_cloud_id, submitted_at=match.submitted_at,
                state_version=expected_version + 1,
                last_polled_at=None, next_reconciliation_at=None,
            ).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise ValueError("attempt compare-and-swap lost")
            receipt.provider_task_id = expected_cloud_id
            receipt.status = "ACCEPTED"
            receipt.finished_at = datetime.now(UTC)
            # Preserve original receipt details and historical failure evidence.
            session.add(AuditEvent(
                action="CLASSIC_ATTACH_VERIFIED_PROVIDER_HANDLE",
                entity_type="generation_attempt", entity_id=str(attempt_id),
                from_state="UNKNOWN_AFTER_SUBMIT", to_state="UNKNOWN_AFTER_SUBMIT",
                reason=("Exact original account/external fingerprint/time evidence; "
                        "resume existing polling only"),
                correlation_id=uuid4().hex,
                evidence_refs_json=[expected_cloud_id, str(receipt.id)],
            ))
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
