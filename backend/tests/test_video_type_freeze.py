"""New unboxing submissions must be frozen before any task or paid work is created."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from libtv_agent.api.video_tasks import create_video_task
from libtv_agent.services.async_video_submission import accept_automatic_video_submission
from libtv_agent.services.fixed_video_tasks import (
    FixedVideoTaskValidationError,
    create_fixed_video_task,
)
from libtv_agent.services.video_type_release import (
    UNBOXING_PAUSED_CODE,
    UNBOXING_VIDEO_GENERATION_ENABLED,
    unboxing_submission_paused,
)


def test_release_gate_preserves_legacy_and_ootd():
    assert UNBOXING_VIDEO_GENERATION_ENABLED is False
    assert unboxing_submission_paused("unboxing")
    assert not unboxing_submission_paused("ootd")
    assert not unboxing_submission_paused("custom")


@pytest.mark.asyncio
async def test_http_rejects_unboxing_before_idempotency_claim():
    with pytest.raises(HTTPException) as error:
        await create_video_task(
            payload=SimpleNamespace(video_type="unboxing"),
            actor=None, session=None, request=None, idempotency_key="unused",
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == UNBOXING_PAUSED_CODE


@pytest.mark.asyncio
async def test_async_acceptance_rejects_before_upload_claim():
    with pytest.raises(FixedVideoTaskValidationError) as error:
        await accept_automatic_video_submission(
            session=None, user_id=None, payload=SimpleNamespace(video_type="unboxing"),
            settings=None,
        )
    assert error.value.code == UNBOXING_PAUSED_CODE


@pytest.mark.asyncio
async def test_sync_route_rejects_before_db_or_paid_work():
    with pytest.raises(FixedVideoTaskValidationError) as error:
        await create_fixed_video_task(
            session=None, user_id=None, payload=SimpleNamespace(video_type="unboxing"),
            settings=None, kling_adapter=None, object_store=None,
        )
    assert error.value.code == UNBOXING_PAUSED_CODE
