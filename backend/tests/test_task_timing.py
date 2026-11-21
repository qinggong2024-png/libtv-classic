import uuid
from datetime import UTC, datetime, timedelta

import pytest
from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase, SagaStatus
from libtv_agent.domain.models import (
    GenerationAttempt,
    GenerationVariant,
    MaterializationSaga,
    ParentTask,
)
from libtv_agent.services.tasks import _build_task_timing

START = datetime(2026, 10, 15, 15, 8, tzinfo=UTC)
STOP = START + timedelta(seconds=6)


def task(phase: ParentTaskPhase = ParentTaskPhase.ACTION_REQUIRED) -> ParentTask:
    return ParentTask(
        phase=phase,
        created_at=START,
        updated_at=STOP,
        source_upload_duration_seconds=0,
        analysis_duration_seconds=0,
        prompt_analysis_duration_seconds=0,
        analysis_attempt_count=0,
        variants=[GenerationVariant(id=uuid.uuid4(), confirmed_at=START)],
    )


def generation_timing(
    status: AttemptStatus,
    phase: ParentTaskPhase = ParentTaskPhase.ACTION_REQUIRED,
    *,
    completed: bool = False,
) -> dict:
    parent = task(phase)
    attempt = GenerationAttempt(
        id=uuid.uuid4(),
        status=status,
        submitted_at=START,
        created_at=START,
        updated_at=STOP,
        completed_at=STOP if completed else None,
    )
    return {
        stage.key: stage
        for stage in _build_task_timing(parent, {parent.variants[0].id: attempt}, {}, None).stages
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AttemptStatus.UNKNOWN_AFTER_SUBMIT, "UNKNOWN"),
        (AttemptStatus.NEEDS_MANUAL_REVIEW, "UNKNOWN"),
        (AttemptStatus.FAILED_BEFORE_SUBMIT, "FAILED"),
        (AttemptStatus.FAILED_TERMINAL, "FAILED"),
        (AttemptStatus.CANCELLED_BEFORE_SUBMIT, "CANCELLED"),
        (AttemptStatus.RUNNING, "INTERRUPTED"),
    ],
)
def test_stopped_generation_uses_last_recorded_time(status: AttemptStatus, expected: str) -> None:
    stages = generation_timing(status)
    assert stages["GENERATION"].status == expected
    assert stages["GENERATION"].duration_seconds == 6
    assert stages["GENERATION"].stopped_at == STOP
    assert stages["GENERATION"].active_started_at is None
    assert not any(stage.status == "ACTIVE" for stage in stages.values())
    assert stages["DOWNLOAD"].status == "PENDING"


def test_failed_completion_is_not_success_and_does_not_start_download() -> None:
    stages = generation_timing(AttemptStatus.FAILED_TERMINAL, completed=True)
    assert stages["GENERATION"].status == "FAILED"
    assert stages["GENERATION"].duration_seconds == 6
    assert stages["DOWNLOAD"].status == "PENDING"


def test_live_generation_keeps_its_clock() -> None:
    stage = generation_timing(AttemptStatus.RUNNING, ParentTaskPhase.GENERATING)["GENERATION"]
    assert stage.status == "ACTIVE"
    assert stage.active_started_at == START
    assert stage.stopped_at is None


def test_successful_generation_starts_download() -> None:
    stages = generation_timing(AttemptStatus.SUCCEEDED, ParentTaskPhase.GENERATING, completed=True)
    assert stages["GENERATION"].status == "COMPLETED"
    assert stages["DOWNLOAD"].status == "ACTIVE"


def test_unknown_duration_does_not_use_later_parent_updates() -> None:
    parent = task()
    parent.updated_at = STOP + timedelta(hours=11)
    attempt = GenerationAttempt(
        id=uuid.uuid4(),
        status=AttemptStatus.UNKNOWN_AFTER_SUBMIT,
        created_at=START,
        submitted_at=START,
        updated_at=STOP,
    )
    stage = next(
        item
        for item in _build_task_timing(parent, {parent.variants[0].id: attempt}, {}, None).stages
        if item.key == "GENERATION"
    )
    assert stage.duration_seconds == 6
    assert stage.stopped_at == STOP


def test_cancelled_task_freezes_running_generation() -> None:
    stage = generation_timing(AttemptStatus.RUNNING, ParentTaskPhase.CANCELLED)["GENERATION"]
    assert stage.status == "CANCELLED"
    assert stage.duration_seconds == 6
    assert stage.active_started_at is None


def test_interrupted_materialization_freezes_at_saga_timestamp() -> None:
    parent = task()
    saga = MaterializationSaga(status=SagaStatus.PENDING, created_at=START, updated_at=STOP)
    stages = {item.key: item for item in _build_task_timing(parent, {}, {}, saga).stages}
    assert stages["MATERIALIZATION"].status == "INTERRUPTED"
    assert stages["MATERIALIZATION"].duration_seconds == 6
    assert stages["MATERIALIZATION"].stopped_at == STOP


def test_task_timing_exposes_read_only_intervals_for_stage_gap_accounting() -> None:
    parent = task(ParentTaskPhase.GENERATING)
    parent.analysis_started_at = START
    parent.analysis_heartbeat_at = START + timedelta(seconds=2)
    parent.analysis_duration_seconds = 2
    attempt = GenerationAttempt(
        id=uuid.uuid4(),
        status=AttemptStatus.SUCCEEDED,
        created_at=START + timedelta(seconds=3),
        submitted_at=START + timedelta(seconds=4),
        updated_at=STOP,
        completed_at=STOP,
    )
    stage_by_key = {
        stage.key: stage
        for stage in _build_task_timing(
            parent, {parent.variants[0].id: attempt}, {}, None
        ).stages
    }
    assert stage_by_key["ANALYSIS"].started_at == START
    assert stage_by_key["ANALYSIS"].ended_at == START + timedelta(seconds=2)
    assert stage_by_key["GENERATION"].started_at == START + timedelta(seconds=4)
    assert stage_by_key["GENERATION"].ended_at == STOP
    assert stage_by_key["DOWNLOAD"].started_at == STOP
    assert stage_by_key["DOWNLOAD"].ended_at is None
    # The stage's 2-second gap does not get misreported as provider processing.
    assert stage_by_key["GENERATION"].duration_seconds == 2


def test_stopped_generation_exposes_frozen_window_not_live_clock() -> None:
    stage = generation_timing(AttemptStatus.UNKNOWN_AFTER_SUBMIT)["GENERATION"]
    assert stage.started_at == START
    assert stage.ended_at == STOP
    assert stage.active_started_at is None

def test_cancelled_analysis_does_not_appear_completed() -> None:
    parent = task(ParentTaskPhase.CANCELLED)
    parent.analysis_started_at = START
    parent.analysis_heartbeat_at = STOP
    parent.analysis_stage = "PROVIDER_RUNNING"
    stage = next(
        item for item in _build_task_timing(parent, {}, {}, None).stages if item.key == "ANALYSIS"
    )
    assert stage.status == "CANCELLED"
    assert stage.duration_seconds == 6
    assert stage.stopped_at == STOP
