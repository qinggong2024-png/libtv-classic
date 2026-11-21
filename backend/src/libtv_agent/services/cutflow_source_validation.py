"""Cutflow eligibility: audio-only historical failures are not a media blocker."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libtv_agent.domain.enums import ValidationStatus
from libtv_agent.domain.models import DownloadArtifact, GenerationAttempt, GenerationVariant, ValidationResult


async def source_technical_ready(
    session: AsyncSession,
    variant: GenerationVariant,
    attempt: GenerationAttempt | None,
    artifact: DownloadArtifact | None,
) -> bool:
    if variant.validation_status in {ValidationStatus.PASSED, ValidationStatus.WARNING}:
        return True
    if variant.validation_status != ValidationStatus.FAILED or attempt is None or artifact is None:
        return False
    if not artifact.has_audio:
        return False
    # A failed legacy result is eligible only when the recorded *sole* issue
    # was an unwanted audio track. Keep all other technical hard gates intact.
    validation = await session.scalar(
        select(ValidationResult)
        .where(
            ValidationResult.attempt_id == attempt.id,
            ValidationResult.coverage == "TECHNICAL",
        )
        .order_by(ValidationResult.created_at.desc(), ValidationResult.id.desc())
        .limit(1)
    )
    return bool(
        validation
        and validation.status == ValidationStatus.FAILED
        and validation.issue_codes_json == ["UNEXPECTED_AUDIO"]
    )
