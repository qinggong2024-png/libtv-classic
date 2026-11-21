"""No network / no paid calls: durable submit and evidence-only recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
)
from libtv_agent.adapters.video_provider import (
    ProviderSubmissionMatch,
    ProviderSubmissionResult,
    ProviderTaskResult,
)
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.enums import AttemptStatus, ChargeStatus, CommandStatus
from libtv_agent.domain.models import (
    Base,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
)
from libtv_agent.runner.supervisor import RunnerSupervisor
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_launch_started_recovery import _stranded_command


@pytest.fixture
async def lab(tmp_path, monkeypatch):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'isolated.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None, app_env="test", kling_cli_enabled=True, runner_root=tmp_path / "runner"
    )
    # Bypass authorization only inside this isolated test with synthetic adapters.
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.real_provider_submission_authorized", lambda _: True
    )
    supervisor = RunnerSupervisor(object(), object(), settings)
    adapter = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="account-a"),
        submit=AsyncMock(
            return_value=ProviderSubmissionResult("cloud-1", "SUBMITTED", 5, "fake", {})
        ),
        query=AsyncMock(return_value=ProviderTaskResult("running", "RUNNING", (), {})),
    )
    supervisor._kling_adapters[settings.kling_cli_provider_name] = adapter
    yield factory, supervisor, adapter
    await db.dispose()


async def ready(factory, supervisor):
    async with factory() as session:
        _, _, attempt, command, _ = await _stranded_command(
            session,
            provider=supervisor.settings.kling_cli_provider_name,
            lease_expires_at=None,
        )
        command.status = CommandStatus.READY
        attempt.intent_hash = attempt.id.hex
        await session.commit()
        return attempt.id, command.id


async def execute(factory, supervisor, command_id):
    async with factory() as session:
        return await supervisor._execute(session, command_id)


async def test_api_parallel_limit_is_durably_queued_and_retried_without_duplicate_receipt(lab):
    factory, supervisor, _ = lab
    attempt_id, command_id = await ready(factory, supervisor)
    provider = supervisor.settings.kling_api_provider_name
    supervisor.settings.kling_api_enabled = True
    api = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="api-account"),
        submit=AsyncMock(side_effect=[
            KlingCliError(
                "KLING_API_RATE_LIMITED", "parallel task over resource pack limit",
                retryable=True,
                details={"httpStatus": 429, "serviceCode": 1302},
            ),
            ProviderSubmissionResult("cloud-after-wait", "SUBMITTED", 5, "fake", {}),
        ]),
    )
    supervisor._kling_adapters[provider] = api
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        attempt.provider = provider
        await session.commit()
    assert await execute(factory, supervisor, command_id) is True
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        receipts = list(await session.scalars(
            select(GenerationSubmissionAttempt).where(
                GenerationSubmissionAttempt.attempt_id == attempt_id
            )
        ))
        assert attempt.status == AttemptStatus.DISPATCH_PENDING, (
            attempt.last_error_code, attempt.error_message
        )
        assert attempt.provider_status == "waiting_capacity"
        assert attempt.task_id is None
        assert command.status == CommandStatus.READY
        assert command.launch_started_at is None
        assert command.lease_expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
        assert len(receipts) == 1
        assert receipts[0].status == "FAILED_BEFORE_SUBMIT"
        assert receipts[0].provider_task_id is None
        command.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await execute(factory, supervisor, command_id) is False
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        receipts = list(await session.scalars(
            select(GenerationSubmissionAttempt)
            .where(GenerationSubmissionAttempt.attempt_id == attempt_id)
            .order_by(GenerationSubmissionAttempt.sequence)
        ))
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == "cloud-after-wait"
        assert attempt.last_error_code is None
        assert [item.status for item in receipts] == ["FAILED_BEFORE_SUBMIT", "ACCEPTED"]
        assert receipts[0].idempotency_key != receipts[1].idempotency_key
    assert api.submit.await_count == 2


async def test_api_cloud_active_capacity_tracks_remote_jobs_not_http_requests(lab):
    factory, supervisor, _ = lab
    provider = supervisor.settings.kling_api_provider_name
    supervisor._kling_adapters[provider] = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="api-account")
    )
    ids = []
    for index in range(supervisor.settings.kling_api_max_active_cloud_tasks):
        attempt_id, _ = await ready(factory, supervisor)
        ids.append(attempt_id)
        async with factory() as session:
            attempt = await session.get(GenerationAttempt, attempt_id)
            attempt.provider = provider
            attempt.provider_account_id = "api-account"
            attempt.status = AttemptStatus.RUNNING
            attempt.task_id = f"remote-job-{index}"
            await session.commit()
    async with factory() as session:
        assert await supervisor._kling_api_at_cloud_capacity(session) is True
        complete = await session.get(GenerationAttempt, ids[0])
        complete.status = AttemptStatus.SUCCEEDED
        await session.commit()
        assert await supervisor._kling_api_at_cloud_capacity(session) is False


@pytest.mark.parametrize("cloud_limit", [5, 20])
async def test_api_fifo_queue_refills_after_one_cloud_slot_frees(lab, monkeypatch, cloud_limit):
    factory, supervisor, _ = lab
    provider = supervisor.settings.kling_api_provider_name
    supervisor.settings.kling_api_enabled = True
    supervisor.settings.kling_api_max_active_cloud_tasks = cloud_limit
    assert supervisor.settings.kling_api_max_active_cloud_tasks == cloud_limit
    supervisor._kling_adapters[provider] = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="api-account")
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.provider_claims_allowed",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.acquire_capacity",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.release_capacity",
        AsyncMock(return_value=1),
    )

    ordered_ids = []
    ordered_commands = []
    for index in range(cloud_limit + 2):
        attempt_id, command_id = await ready(factory, supervisor)
        ordered_ids.append(attempt_id)
        ordered_commands.append(command_id)
        async with factory() as session:
            attempt = await session.get(GenerationAttempt, attempt_id)
            command = await session.get(GenerationCommand, command_id)
            attempt.provider = provider
            attempt.status = AttemptStatus.DISPATCH_PENDING
            # Explicit microseconds test FIFO even when arrivals share a second.
            command.created_at = datetime(2026, 11, 11, tzinfo=UTC) + timedelta(
                microseconds=index
            )
            await session.commit()

    started = []

    async def fake_execute(session, command_id):
        command = await session.get(GenerationCommand, command_id)
        attempt = await session.get(GenerationAttempt, command.attempt_id)
        started.append(command.id)
        attempt.status = AttemptStatus.RUNNING
        attempt.provider_account_id = "api-account"
        attempt.task_id = f"cloud-{len(started)}"
        command.status = CommandStatus.PROCESS_STARTED
        await session.commit()
        return False

    supervisor._execute = fake_execute
    async with factory() as session:
        assert await supervisor._run_ready_provider(session, "kling") == cloud_limit
        assert started == ordered_commands[:cloud_limit]
        assert await supervisor._kling_api_at_cloud_capacity(session) is True
        queued = await session.get(GenerationAttempt, ordered_ids[cloud_limit])
        assert queued.status == AttemptStatus.DISPATCH_PENDING

        # Free one slot. The oldest queued result is submitted automatically.
        active = await session.get(GenerationAttempt, ordered_ids[0])
        active.status = AttemptStatus.SUCCEEDED
        await session.commit()
        assert await supervisor._run_ready_provider(session, "kling") == 1
        assert started == ordered_commands[:cloud_limit + 1]

        active = await session.get(GenerationAttempt, ordered_ids[1])
        active.status = AttemptStatus.SUCCEEDED
        await session.commit()
        assert await supervisor._run_ready_provider(session, "kling") == 1
        assert started == ordered_commands
        assert await supervisor._kling_api_at_cloud_capacity(session) is True


async def _unknown_api_job(factory, supervisor, *, task_id="known-cloud-id", minutes_old=1):
    """Create one synthetic, previously accepted job, with no paid submission."""
    attempt_id, command_id = await ready(factory, supervisor)
    provider = supervisor.settings.kling_api_provider_name
    supervisor.settings.kling_api_enabled = True
    # A synthetic key enables querying existing jobs; all calls use the mock adapter.
    supervisor.settings.kling_api_key = "synthetic-test-only"
    adapter = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="api-account"),
        query=AsyncMock(),
        submit=AsyncMock(),
    )
    supervisor._kling_adapters[provider] = adapter
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.provider = provider
        attempt.provider_account_id = "api-account"
        attempt.task_id = task_id
        attempt.submitted_at = datetime.now(UTC) - timedelta(minutes=minutes_old)
        attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
        attempt.charge_status = ChargeStatus.UNKNOWN
        command.status = CommandStatus.UNCERTAIN
        await session.commit()
    return attempt_id, command_id, adapter


async def test_unknown_api_terminal_failure_releases_cloud_slot_without_new_submit(lab):
    factory, supervisor, _ = lab
    attempt_id, command_id, adapter = await _unknown_api_job(factory, supervisor)
    supervisor.settings.kling_api_max_active_cloud_tasks = 1
    adapter.query.return_value = ProviderTaskResult(
        "failed", "failed", (), {"id": "known-cloud-id"}
    )
    async with factory() as session:
        assert await supervisor._kling_api_at_cloud_capacity(session) is True
        await supervisor.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        assert attempt.status == AttemptStatus.FAILED_TERMINAL
        assert attempt.charge_status == ChargeStatus.UNKNOWN
        assert command.status == CommandStatus.TERMINAL
        assert await supervisor._kling_api_at_cloud_capacity(session) is False
    adapter.query.assert_awaited_once_with("known-cloud-id")
    adapter.submit.assert_not_awaited()


@pytest.mark.parametrize(
    ("billing", "expected_charge"),
    [
        ([{"amount": "0"}], ChargeStatus.NOT_CHARGED),
        ([{"amount": "0", "charge_type": "unit"}], ChargeStatus.NOT_CHARGED),
        ([{"amount": "0"}, {"amount": "0.00"}], ChargeStatus.NOT_CHARGED),
        ([], ChargeStatus.UNKNOWN),
        (None, ChargeStatus.UNKNOWN),
        ([{"amount": "0"}, {"amount": "5"}], ChargeStatus.UNKNOWN),
        ([{"amount": "0"}, {"amount": "n/a"}], ChargeStatus.UNKNOWN),
    ],
)
async def test_kling_risk_control_failure_preserves_receipt_and_never_retries(
    lab, billing, expected_charge,
):
    factory, supervisor, _ = lab
    attempt_id, command_id, adapter = await _unknown_api_job(factory, supervisor)
    raw = {"id": "known-cloud-id", "status": "failed"}
    if billing is not None:
        raw["billing"] = billing
    adapter.query.return_value = ProviderTaskResult(
        "failed", "failed", (), raw,
        message="Failure to pass the risk control system",
    )
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        assert attempt.status == AttemptStatus.FAILED_TERMINAL
        assert attempt.last_error_code == "KLING_REMOTE_RISK_CONTROL_REJECTED"
        assert attempt.error_message == "Failure to pass the risk control system"
        assert attempt.provider_error_retryable is False
        assert attempt.charge_status == expected_charge
        assert command.status == CommandStatus.TERMINAL
        if billing is not None:
            assert attempt.raw_response_json["billing"] == billing
        else:
            assert "billing" not in attempt.raw_response_json
        if expected_charge == ChargeStatus.NOT_CHARGED:
            assert attempt.credits_consumed == 0
        else:
            assert attempt.credits_consumed is None
    adapter.submit.assert_not_awaited()


async def test_kling_generic_cloud_failure_does_not_claim_risk_control_or_zero_charge(lab):
    factory, supervisor, _ = lab
    attempt_id, _, adapter = await _unknown_api_job(factory, supervisor)
    adapter.query.return_value = ProviderTaskResult(
        "failed", "failed", (), {"id": "known-cloud-id", "billing": [{"amount": "5"}]},
        message="Some unrelated remote error",
    )
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.last_error_code == "KLING_REMOTE_TASK_FAILED"
        assert attempt.charge_status == ChargeStatus.UNKNOWN
    adapter.submit.assert_not_awaited()


async def test_unknown_api_running_rejoins_normal_polling_with_original_handle(lab):
    factory, supervisor, _ = lab
    attempt_id, command_id, adapter = await _unknown_api_job(factory, supervisor)
    adapter.query.return_value = ProviderTaskResult(
        "running", "processing", (), {"id": "known-cloud-id"}
    )
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == "known-cloud-id"
        assert command.status == CommandStatus.PROCESS_STARTED
    adapter.submit.assert_not_awaited()


async def test_overdue_unknown_api_keeps_cloud_slot_until_remote_terminal(lab):
    factory, supervisor, _ = lab
    attempt_id, _, adapter = await _unknown_api_job(factory, supervisor, minutes_old=40)
    supervisor.settings.kling_api_max_active_cloud_tasks = 1
    adapter.query.return_value = ProviderTaskResult(
        "running", "processing", (), {"id": "known-cloud-id"}
    )
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        assert attempt.provider_status == "running"
        assert attempt.next_reconciliation_at.replace(tzinfo=UTC) > datetime.now(UTC)
        assert await supervisor._kling_api_at_cloud_capacity(session) is True
        assert await supervisor.poll_kling_tasks(session) == 0
        assert adapter.query.await_count == 1

        # Remote eventually finishes: only that positive evidence frees capacity.
        attempt.next_reconciliation_at = datetime.now(UTC) - timedelta(seconds=1)
        attempt.last_polled_at = datetime.now(UTC) - timedelta(minutes=3)
        await session.commit()
        adapter.query.return_value = ProviderTaskResult(
            "failed", "failed", (), {"id": "known-cloud-id"}
        )
        assert await supervisor.poll_kling_tasks(session) == 1
        assert attempt.status == AttemptStatus.FAILED_TERMINAL
        assert await supervisor._kling_api_at_cloud_capacity(session) is False
    adapter.submit.assert_not_awaited()


async def test_unknown_api_query_outage_retries_read_only_after_exhaustion(lab):
    factory, supervisor, _ = lab
    attempt_id, _, adapter = await _unknown_api_job(factory, supervisor)
    supervisor.settings.kling_api_max_retries = 1
    adapter.query.side_effect = KlingCliError(
        "KLING_API_QUERY_FAILED", "temporarily down", retryable=True
    )
    async with factory() as session:
        for _ in range(5):
            attempt = await session.get(GenerationAttempt, attempt_id)
            attempt.next_reconciliation_at = None
            attempt.last_polled_at = None
            await session.commit()
            assert await supervisor.poll_kling_tasks(session) == 1
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        assert attempt.task_id == "known-cloud-id"
        assert attempt.next_reconciliation_at is not None
        assert attempt.next_reconciliation_at.replace(tzinfo=UTC) > datetime.now(UTC)
        assert attempt.last_error_code == "KLING_RECONCILIATION_EXHAUSTED"
        assert await supervisor.poll_kling_tasks(session) == 0
    assert adapter.query.await_count == 5
    adapter.submit.assert_not_awaited()


async def test_unknown_api_cloud_success_resumes_original_delivery(lab, monkeypatch):
    from libtv_agent.adapters.video_provider import ProviderOutput

    factory, supervisor, _ = lab
    _, _, adapter = await _unknown_api_job(factory, supervisor)
    adapter.query.return_value = ProviderTaskResult(
        "succeeded", "succeeded",
        (ProviderOutput("https://synthetic.invalid/recovered.mp4"),),
        {"id": "known-cloud-id"},
    )
    delivery = AsyncMock()
    monkeypatch.setattr(supervisor, "_handle_success", delivery)
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
    delivery.assert_awaited_once()
    assert delivery.call_args.args[3].payload["taskId"] == "known-cloud-id"
    assert delivery.call_args.kwargs["download_adapter"] is adapter
    adapter.submit.assert_not_awaited()


async def test_unknown_api_wrong_account_cannot_free_or_query_cloud_job(lab):
    factory, supervisor, _ = lab
    attempt_id, _, adapter = await _unknown_api_job(factory, supervisor)
    adapter.submission_account_id.return_value = "different-account"
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 1
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.task_id == "known-cloud-id"
        assert attempt.last_error_code == "KLING_ACCOUNT_CHANGED"
        assert attempt.charge_status == ChargeStatus.UNKNOWN
    adapter.query.assert_not_awaited()
    adapter.submit.assert_not_awaited()


async def test_unknown_api_without_handle_is_not_polled_or_released(lab):
    factory, supervisor, _ = lab
    attempt_id, _, adapter = await _unknown_api_job(factory, supervisor, task_id=None)
    async with factory() as session:
        assert await supervisor.poll_kling_tasks(session) == 0
        assert (
            await session.get(GenerationAttempt, attempt_id)
        ).status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
    adapter.query.assert_not_awaited()
    adapter.submit.assert_not_awaited()


async def test_api_deferred_oldest_blocks_newer_submission(lab, monkeypatch):
    factory, supervisor, _ = lab
    provider = supervisor.settings.kling_api_provider_name
    supervisor.settings.kling_api_enabled = True
    supervisor._kling_adapters[provider] = SimpleNamespace(
        submission_account_id=AsyncMock(return_value="api-account")
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.provider_claims_allowed",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.acquire_capacity",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "libtv_agent.runner.supervisor.release_capacity",
        AsyncMock(return_value=1),
    )
    ids = []
    for index in range(2):
        attempt_id, command_id = await ready(factory, supervisor)
        ids.append(command_id)
        async with factory() as session:
            attempt = await session.get(GenerationAttempt, attempt_id)
            command = await session.get(GenerationCommand, command_id)
            attempt.provider = provider
            attempt.status = AttemptStatus.DISPATCH_PENDING
            command.created_at = datetime(2026, 11, 11, tzinfo=UTC) + timedelta(
                microseconds=index
            )
            if index == 0:
                command.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            await session.commit()
    started = []

    async def fake_execute(session, command_id):
        command = await session.get(GenerationCommand, command_id)
        attempt = await session.get(GenerationAttempt, command.attempt_id)
        started.append(command_id)
        command.status = CommandStatus.PROCESS_STARTED
        attempt.status = AttemptStatus.RUNNING
        attempt.provider_account_id = "api-account"
        attempt.task_id = f"remote-{len(started)}"
        await session.commit()
        return False

    supervisor._execute = fake_execute
    async with factory() as session:
        assert await supervisor._run_ready_provider(session, "kling") == 0
        assert started == []
        first = await session.get(GenerationCommand, ids[0])
        first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await supervisor._run_ready_provider(session, "kling") == 2
        assert started == ids


async def test_normal_submit_is_durable_before_call_and_handle_is_saved(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)

    async def accepted(request):
        async with factory() as session:
            record = await session.scalar(select(GenerationSubmissionAttempt))
            assert record.status == "STARTED"
            assert record.attempt_id == attempt_id
        return ProviderSubmissionResult("cloud-1", "SUBMITTED", 5, "fake", {})

    adapter.submit.side_effect = accepted
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        record = await session.scalar(select(GenerationSubmissionAttempt))
        assert attempt.task_id == record.provider_task_id == "cloud-1"
        assert attempt.status == AttemptStatus.RUNNING
        assert record.status == "ACCEPTED"


async def test_api_unknown_submit_recovers_exact_handle_without_paid_retry(lab):
    factory, supervisor, _ = lab
    attempt_id, command_id = await ready(factory, supervisor)
    provider = supervisor.settings.kling_api_provider_name
    start = datetime.now(UTC) - timedelta(seconds=45)
    created = start + timedelta(seconds=40)
    adapter = SimpleNamespace(
        find_recent_submission=AsyncMock(return_value=ProviderSubmissionMatch(
            provider_task_id="api-recovered", account_id="api-bound-account",
            input_fingerprint=attempt_id.hex, submitted_at=created,
            raw_response={"id": "api-recovered"},
        )),
        submit=AsyncMock(),
    )
    supervisor._kling_adapters[provider] = adapter
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.provider = provider
        attempt.provider_account_id = "api-bound-account"
        attempt.submission_started_at = start
        attempt.status = AttemptStatus.RECONCILING
        command.status = CommandStatus.UNCERTAIN
        await session.commit()

    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        await supervisor._advance_reconciliation_without_handle(session, command, attempt)
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == "api-recovered"
        assert command.status == CommandStatus.PROCESS_STARTED

    assert adapter.find_recent_submission.await_count == 1
    options = adapter.find_recent_submission.await_args.kwargs
    assert options["input_fingerprint"] == attempt_id.hex
    assert options["account_id"] == "api-bound-account"
    assert (options["submitted_before"] - options["submitted_after"]).total_seconds() >= (
        supervisor.settings.kling_api_request_timeout_seconds
    )
    adapter.submit.assert_not_awaited()


async def test_timeout_cloud_accepted_queries_original_handle(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.submit.side_effect = KlingCliError(
        "KLING_CLI_TIMEOUT",
        "response lost",
        uncertain=True,
        provider_task_id="accepted-1",
    )
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.task_id == "accepted-1"
        attempt.next_reconciliation_at = None
        await session.commit()
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
        assert (await session.get(GenerationAttempt, attempt_id)).status == AttemptStatus.RUNNING
    adapter.query.assert_awaited_once_with("accepted-1")
    assert adapter.submit.await_count == 1


async def test_failure_before_submission_does_not_claim_cloud_acceptance(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.submission_account_id.side_effect = KlingCliError(
        "KLING_ACCOUNT_UNVERIFIED", "no identity"
    )
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        assert (
            await session.get(GenerationAttempt, attempt_id)
        ).status == AttemptStatus.FAILED_BEFORE_SUBMIT
        assert await session.scalar(select(GenerationSubmissionAttempt)) is None
    adapter.submit.assert_not_awaited()


async def test_duplicate_requests_cannot_resubmit(lab):
    factory, supervisor, adapter = lab
    _, command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, command_id)
    await execute(factory, supervisor, command_id)
    assert adapter.submit.await_count == 1


async def test_handle_survives_later_local_bookkeeping_failure(lab, monkeypatch):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)

    def broken(*_):
        raise RuntimeError("local bookkeeping stopped")

    monkeypatch.setattr("libtv_agent.runner.supervisor.record_pipeline_submission", broken)
    with pytest.raises(RuntimeError):
        await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.task_id == "cloud-1"
        assert attempt.status == AttemptStatus.RUNNING
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
    assert adapter.submit.await_count == 1
    adapter.query.assert_awaited_once_with("cloud-1")


async def test_cloud_success_continues_existing_delivery(lab, monkeypatch):
    from libtv_agent.adapters.video_provider import ProviderOutput

    factory, supervisor, adapter = lab
    _, command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, command_id)
    adapter.query.return_value = ProviderTaskResult(
        "succeeded", "COMPLETED", (ProviderOutput("https://synthetic.invalid/video.mp4"),), {}
    )
    delivery = AsyncMock()
    monkeypatch.setattr(supervisor, "_handle_success", delivery)
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
    delivery.assert_awaited_once()
    assert delivery.call_args.args[3].payload["taskId"] == "cloud-1"
    assert delivery.call_args.kwargs["download_adapter"] is adapter
    assert adapter.submit.await_count == 1


async def test_restart_after_marker_recovers_without_submit(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        command.status = CommandStatus.LAUNCH_STARTED
        command.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(
            GenerationSubmissionAttempt(
                attempt_id=attempt_id,
                sequence=1,
                provider=attempt.provider,
                idempotency_key=f"{attempt_id.hex}:generation",
                status="STARTED",
            )
        )
        await session.commit()
    restarted = RunnerSupervisor(object(), object(), supervisor.settings)
    async with factory() as session:
        assert await restarted.recover_stale_launch_started_commands(session) == 1
        assert (
            await session.get(GenerationAttempt, attempt_id)
        ).status == AttemptStatus.RECONCILING
    adapter.submit.assert_not_awaited()


async def test_missing_handle_stops_after_bounded_attempts_and_never_guesses(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.submit.side_effect = KlingCliError("KLING_CLI_TIMEOUT", "lost", uncertain=True)
    await execute(factory, supervisor, command_id)
    for _ in range(6):
        async with factory() as session:
            attempt = await session.get(GenerationAttempt, attempt_id)
            attempt.next_reconciliation_at = None
            attempt.last_polled_at = None
            await session.commit()
            await supervisor.poll_kling_tasks(session)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        assert attempt.task_id is None
        assert attempt.reconciliation_attempt_count == 5
        assert attempt.last_error_code == "KLING_RECONCILIATION_EXHAUSTED"
    adapter.query.assert_not_awaited()
    assert adapter.submit.await_count == 1


async def test_unsupported_history_stops_immediately_and_preserves_submit_error(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.submit.side_effect = KlingCliError(
        "KLING_RATE_LIMITED", "rate limit without verified rejection", uncertain=True,
    )
    adapter.find_recent_submission = AsyncMock(side_effect=KlingCliError(
        "KLING_HISTORY_QUERY_UNSUPPORTED", "CLI history not supported",
    ))
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        attempt.next_reconciliation_at = None
        await session.commit()
        await supervisor.poll_kling_tasks(session)
        await supervisor.poll_kling_tasks(session)
        assert attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
        assert attempt.last_error_code == "KLING_RATE_LIMITED"
        assert attempt.reconciliation_attempt_count == 0
        assert attempt.provider_error_retryable is False
    adapter.find_recent_submission.assert_awaited_once()
    adapter.submit.assert_awaited_once()


async def test_account_switch_never_queries_under_wrong_account(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, command_id)
    adapter.submission_account_id.return_value = "account-b"
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.last_error_code == "KLING_ACCOUNT_CHANGED"
        assert attempt.task_id == "cloud-1"
    adapter.query.assert_not_awaited()


async def test_cloud_query_failure_obeys_backoff_and_preserves_handle(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, command_id)
    adapter.query.side_effect = RuntimeError("private upstream message")
    async with factory() as session:
        await supervisor.poll_kling_tasks(session)
        await supervisor.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        assert attempt.task_id == "cloud-1"
        assert attempt.status == AttemptStatus.RECONCILING
        assert "private" not in attempt.error_message
    assert adapter.query.await_count == 1


async def test_concurrent_duplicate_commands_call_submit_once(lab):
    factory, supervisor, adapter = lab
    _, command_id = await ready(factory, supervisor)
    await asyncio.gather(
        execute(factory, supervisor, command_id), execute(factory, supervisor, command_id)
    )
    assert adapter.submit.await_count == 1


async def test_independent_concurrent_tasks_have_separate_durable_operations(lab):
    factory, supervisor, adapter = lab
    first, command_a = await ready(factory, supervisor)
    second, command_b = await ready(factory, supervisor)

    async def accepted(request):
        return ProviderSubmissionResult(request.task_trace_id, "SUBMITTED", None, "fake", {})

    adapter.submit.side_effect = accepted
    await asyncio.gather(
        execute(factory, supervisor, command_a), execute(factory, supervisor, command_b)
    )
    assert adapter.submit.await_count == 2
    async with factory() as session:
        assert (await session.get(GenerationAttempt, first)).task_id == first.hex
        assert (await session.get(GenerationAttempt, second)).task_id == second.hex
        records = list(await session.scalars(select(GenerationSubmissionAttempt)))
        assert len({record.idempotency_key for record in records}) == 2


async def test_old_unknown_task_does_not_block_unrelated_new_task(lab):
    factory, supervisor, adapter = lab
    old_id, _ = await ready(factory, supervisor)
    async with factory() as session:
        old = await session.get(GenerationAttempt, old_id)
        old.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
        await session.commit()
    _, command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, command_id)
    assert adapter.submit.await_count == 1


@pytest.mark.parametrize("fault", ["account", "fingerprint", "time", "missing"])
async def test_history_recovery_rejects_inexact_evidence(lab, fault):
    factory, supervisor, adapter = lab
    attempt_id, _ = await ready(factory, supervisor)
    now = datetime.now(UTC)
    adapter.find_recent_submission = AsyncMock(
        return_value=ProviderSubmissionMatch(
            "cloud-history",
            "wrong" if fault == "account" else "account-a",
            "wrong" if fault == "fingerprint" else attempt_id.hex,
            now - timedelta(hours=1) if fault == "time" else now,
        )
    )
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        attempt.provider_account_id = None if fault == "missing" else "account-a"
        attempt.submission_started_at = now - timedelta(seconds=1)
        attempt.submission_finished_at = now + timedelta(seconds=1)
        with pytest.raises(KlingCliError):
            await supervisor._recover_history_handle(session, attempt, adapter)
        assert attempt.task_id is None


@pytest.mark.parametrize("output", ["not JSON", '{"ok":true,"body":[]}'])
async def test_malformed_submission_response_is_uncertain(tmp_path, output):
    runner = AsyncMock(return_value=CliProcessResult(0, output, ""))
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, runner_root=tmp_path), process_runner=runner
    )
    with pytest.raises(KlingCliError) as exc:
        await adapter._invoke("image_to_video", submitted=True)
    assert exc.value.uncertain


@pytest.mark.parametrize("receipt_status", ["ACCEPTED", "UNCERTAIN"])
async def test_restart_recovers_exact_saved_receipt_before_unsupported_history(lab, receipt_status):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.find_recent_submission = AsyncMock(side_effect=AssertionError(
        "durable receipt must be read before attempting cloud history"
    ))
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.provider_account_id = "account-a"
        command.status = CommandStatus.LAUNCH_STARTED
        command.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint=attempt.intent_hash, status=receipt_status,
            provider_task_id="durable-cloud-id",
        ))
        await session.commit()
    restarted = RunnerSupervisor(object(), object(), supervisor.settings)
    restarted._kling_adapters[supervisor.settings.kling_cli_provider_name] = adapter
    async with factory() as session:
        assert await restarted.recover_stale_launch_started_commands(session) == 1
        attempt = await session.get(GenerationAttempt, attempt_id)
        attempt.next_reconciliation_at = None
        await session.commit()
        await restarted.poll_kling_tasks(session)
        assert attempt.task_id == "durable-cloud-id"
        assert attempt.status == AttemptStatus.RUNNING
        attempt.last_polled_at = None
        await session.commit()
        await restarted.poll_kling_tasks(session)
    adapter.query.assert_awaited_once_with("durable-cloud-id")
    adapter.find_recent_submission.assert_not_awaited()
    adapter.submit.assert_not_awaited()


@pytest.mark.parametrize("fault", ["fingerprint", "provider", "status"])
async def test_saved_receipt_mismatch_cannot_bind_an_unrelated_cloud_handle(lab, fault):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    adapter.find_recent_submission = AsyncMock()
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.status = AttemptStatus.RECONCILING
        command.status = CommandStatus.UNCERTAIN
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1,
            provider="wrong-provider" if fault == "provider" else attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint="wrong-input" if fault == "fingerprint" else attempt.intent_hash,
            status="FAILED_BEFORE_SUBMIT" if fault == "status" else "ACCEPTED",
            provider_task_id="must-not-use",
        ))
        await session.commit()
        await supervisor.poll_kling_tasks(session)
        assert attempt.task_id is None
        assert attempt.last_error_code == "KLING_RECEIPT_EVIDENCE_MISMATCH"
        record = await session.scalar(select(GenerationSubmissionAttempt))
        assert record.provider_task_id == "must-not-use"
    adapter.find_recent_submission.assert_not_awaited()
    adapter.query.assert_not_awaited()
    adapter.submit.assert_not_awaited()


async def test_replacement_never_reuses_previous_depth_receipt(lab):
    from libtv_agent.runner.deep_video_pipeline import save_pipeline

    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        save_pipeline(command, {"stage": "replacement", "depthTaskId": "previous-depth"})
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:depth",
            input_fingerprint=attempt.intent_hash, status="ACCEPTED",
            provider_task_id="previous-depth",
        ))
        await session.commit()
        assert not await supervisor._restore_submission_receipt_handle(session, command, attempt)
        assert attempt.task_id is None
    adapter.submit.assert_not_awaited()


@pytest.mark.parametrize("fault", ["fingerprint", "status"])
async def test_duplicate_command_with_bad_receipt_preserves_original_evidence(lab, fault):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint="mismatched" if fault == "fingerprint" else attempt.intent_hash,
            status="FAILED_BEFORE_SUBMIT" if fault == "status" else "ACCEPTED",
            provider_task_id="do-not-erase",
        ))
        await session.commit()
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        record = await session.scalar(select(GenerationSubmissionAttempt))
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.task_id is None
        assert record.provider_task_id == "do-not-erase"
        assert record.status == ("FAILED_BEFORE_SUBMIT" if fault == "status" else "ACCEPTED")
    adapter.submit.assert_not_awaited()


async def test_unique_constraint_blocks_second_active_attempt(lab):
    factory, supervisor, _ = lab
    attempt_id, _ = await ready(factory, supervisor)
    async with factory() as session:
        existing = await session.get(GenerationAttempt, attempt_id)
        session.add(
            GenerationAttempt(variant_id=existing.variant_id, status=AttemptStatus.RECONCILING)
        )
        with pytest.raises(IntegrityError):
            await session.commit()
