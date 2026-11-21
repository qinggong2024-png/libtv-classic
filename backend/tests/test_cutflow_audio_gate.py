"""Audio is a CUT concern, not an original-video handoff blocker."""

import pytest

from libtv_agent.domain.enums import ValidationStatus
from libtv_agent.domain.models import Asset, DownloadArtifact, GenerationAttempt, GenerationVariant, ValidationResult
from libtv_agent.services.cutflow_handoff import CutflowHandoffError, create_cutflow_handoff
from libtv_agent.services.cutflow_multi import snapshot_sources
from libtv_agent.services.cutflow_source_validation import source_technical_ready
from test_cutflow_handoff import handoff_env


@pytest.mark.asyncio
async def test_historical_audio_only_failure_eligible_for_single_and_unified_cut(handoff_env):
    database, settings, store, ids = handoff_env
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        variant = await session.get(GenerationVariant, ids["variant"])
        artifact.has_audio = True
        artifact.size_bytes = 15
        artifact.duration_seconds = 10.04
        variant.validation_status = ValidationStatus.FAILED
        session.add(ValidationResult(
            attempt_id=ids["attempt"], status=ValidationStatus.FAILED,
            coverage="TECHNICAL", rule_version="1.0.0",
            issue_codes_json=["UNEXPECTED_AUDIO"],
        ))
        session.add(Asset(
            parent_task_id=ids["task"], role="PRODUCT", order_no=1,
            original_filename="product.jpg", mime_type="image/jpeg",
            object_key="products/product.jpg", sha256="a" * 64,
            size_bytes=100, width=100, height=100,
        ))
        await session.commit()
        handoff = await create_cutflow_handoff(
            session, settings=settings, object_store=store, user_id=ids["user"],
            task_id=ids["task"], variant_id=ids["variant"],
        )
        sources = await snapshot_sources(
            session, ids["user"], [ids["variant"]], allow_single_source=True,
        )
    assert handoff.status == "QUEUED"
    assert sources[0]["artifactId"] == str(ids["artifact"])


@pytest.mark.asyncio
@pytest.mark.parametrize("issues, has_audio, expected", [
    (["UNEXPECTED_AUDIO"], True, True),
    (["UNEXPECTED_AUDIO", "DURATION_OUT_OF_RANGE"], True, False),
    (["ASPECT_RATIO_INVALID"], True, False),
    (["UNEXPECTED_AUDIO"], False, False),
    ([], True, False),
])
async def test_failed_validation_only_bypasses_audio_issue(
    handoff_env, issues, has_audio, expected,
):
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        variant = await session.get(GenerationVariant, ids["variant"])
        attempt = await session.get(GenerationAttempt, ids["attempt"])
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        variant.validation_status = ValidationStatus.FAILED
        artifact.has_audio = has_audio
        session.add(ValidationResult(
            attempt_id=ids["attempt"], status=ValidationStatus.FAILED,
            coverage="TECHNICAL", rule_version="1.0.0", issue_codes_json=issues,
        ))
        await session.flush()
        assert await source_technical_ready(session, variant, attempt, artifact) is expected


@pytest.mark.asyncio
async def test_non_audio_failure_still_blocks_single_cut(handoff_env):
    database, settings, store, ids = handoff_env
    async with database.session_factory() as session:
        variant = await session.get(GenerationVariant, ids["variant"])
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        variant.validation_status = ValidationStatus.FAILED
        artifact.has_audio = True
        session.add(ValidationResult(
            attempt_id=ids["attempt"], status=ValidationStatus.FAILED,
            coverage="TECHNICAL", rule_version="1.0.0",
            issue_codes_json=["UNEXPECTED_AUDIO", "ASPECT_RATIO_INVALID"],
        ))
        await session.commit()
        with pytest.raises(CutflowHandoffError) as exc:
            await create_cutflow_handoff(
                session, settings=settings, object_store=store, user_id=ids["user"],
                task_id=ids["task"], variant_id=ids["variant"],
            )
        assert exc.value.code == "VIDEO_NOT_READY"
