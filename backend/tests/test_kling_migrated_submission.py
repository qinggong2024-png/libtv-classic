"""Exercise the runner against the installed migration, with a synthetic provider."""

import importlib.util
from pathlib import Path

import pytest
import test_kling_submission_reconciliation as reconciliation
from alembic.migration import MigrationContext
from alembic.operations import Operations
from libtv_agent.adapters.kling_submission_receipts import KlingSubmissionReceipt
from libtv_agent.domain.enums import AttemptStatus, ChargeStatus, CommandStatus, ReservationStatus
from libtv_agent.domain.models import (
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    PointsReservation,
)
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from test_kling_submission_reconciliation import execute, ready

lab = reconciliation.lab
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/c2a6f8d1e4b0_kling_submission_reconciliation.py"
)


async def install_submission_migration(factory):
    spec = importlib.util.spec_from_file_location("installed_kling_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def install_real_migration(connection):
        GenerationSubmissionAttempt.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        columns = inspect(connection).get_columns("generation_submission_attempt")
        # Existing LAN installations have this NOT NULL column without a default.
        started_at = next(column for column in columns if column["name"] == "started_at")
        assert not started_at["nullable"]
        assert started_at["default"] is None

    async with factory.kw["bind"].begin() as connection:
        await connection.run_sync(install_real_migration)


async def test_migrated_database_persists_receipt_before_provider_call(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    submission = adapter.submit.return_value

    async def accepted(request):
        async with factory() as session:
            receipt = await session.scalar(select(GenerationSubmissionAttempt))
            assert receipt.attempt_id == attempt_id
            assert receipt.status == "STARTED"
            assert receipt.started_at is not None
        return submission

    adapter.submit.side_effect = accepted
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        receipt = await session.scalar(select(GenerationSubmissionAttempt))
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.task_id == receipt.provider_task_id == "cloud-1"
        assert receipt.status == "ACCEPTED"
    adapter.submit.assert_awaited_once()


@pytest.mark.parametrize(
    "database_error", [IntegrityError, OperationalError], ids=["constraint", "database-busy"]
)
async def test_receipt_write_failure_does_not_claim_remote_submission(lab, database_error):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    engine = factory.kw["bind"].sync_engine

    def fail_receipt_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO generation_submission_attempt "):
            raise database_error(statement, parameters, RuntimeError("synthetic write failure"))

    event.listen(engine, "before_cursor_execute", fail_receipt_insert)
    try:
        async with factory() as session:
            assert await supervisor._execute(session, command_id) is True
            attempt = await session.get(GenerationAttempt, attempt_id)
            command = await session.get(GenerationCommand, command_id)
            reservation = await session.scalar(
                select(PointsReservation).where(PointsReservation.attempt_id == attempt_id)
            )
            assert attempt.status == AttemptStatus.FAILED_BEFORE_SUBMIT
            assert attempt.charge_status == ChargeStatus.NOT_CHARGED
            assert attempt.last_error_code == "KLING_SUBMISSION_RECORD_FAILED"
            assert command.status == CommandStatus.TERMINAL
            assert reservation.status == ReservationStatus.RELEASED
            assert await session.scalar(select(GenerationSubmissionAttempt)) is None
    finally:
        event.remove(engine, "before_cursor_execute", fail_receipt_insert)
    adapter.submit.assert_not_awaited()

    # A local write fault must not strand the queue or poison later transactions.
    _, next_command_id = await ready(factory, supervisor)
    await execute(factory, supervisor, next_command_id)
    adapter.submit.assert_awaited_once()


@pytest.mark.parametrize("database_error", [IntegrityError, OperationalError])
async def test_accepted_receipt_write_fault_preserves_id_and_queue(lab, database_error):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    engine = factory.kw["bind"].sync_engine
    failed = False

    def fail_accepted_update(connection, cursor, statement, parameters, context, many):
        nonlocal failed
        if not failed and statement.startswith("UPDATE generation_submission_attempt SET"):
            failed = True
            raise database_error(statement, parameters, RuntimeError("synthetic update fault"))

    event.listen(engine, "before_cursor_execute", fail_accepted_update)
    try:
        await execute(factory, supervisor, command_id)
    finally:
        event.remove(engine, "before_cursor_execute", fail_accepted_update)
    assert failed
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        receipt = await session.scalar(select(GenerationSubmissionAttempt))
        assert attempt.task_id == receipt.provider_task_id == "cloud-1"
        assert attempt.status == AttemptStatus.RECONCILING
        assert attempt.last_error_code == "KLING_ACCEPTED_RECEIPT_DB_WRITE_FAILED"
        attempt.next_reconciliation_at = None
        attempt.last_polled_at = None
        await session.commit()
        await supervisor.poll_kling_tasks(session)
        assert attempt.status == AttemptStatus.RUNNING
    adapter.submit.assert_awaited_once()
    adapter.query.assert_awaited_once_with("cloud-1")
    _, later_command = await ready(factory, supervisor)
    await execute(factory, supervisor, later_command)
    assert adapter.submit.await_count == 2


async def test_restart_restores_cli_journal_when_db_has_only_launch_marker(lab):
    from libtv_agent.runner.supervisor import RunnerSupervisor

    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.provider_account_id = "account-a"
        attempt.status = AttemptStatus.RECONCILING
        command.status = CommandStatus.UNCERTAIN
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint=attempt.intent_hash, status="STARTED",
        ))
        journal = KlingSubmissionReceipt(
            supervisor.settings.runner_root, f"{attempt_id.hex}:generation", attempt.provider,
            attempt.provider_account_id, attempt.intent_hash,
        )
        journal.begin()
        journal.finish("ACCEPTED", task_id="journal-cloud-id", credits=7)
        await session.commit()
    restarted = RunnerSupervisor(object(), object(), supervisor.settings)
    restarted._kling_adapters[supervisor.settings.kling_cli_provider_name] = adapter
    async with factory() as session:
        await restarted.poll_kling_tasks(session)
        attempt = await session.get(GenerationAttempt, attempt_id)
        saved = await session.scalar(select(GenerationSubmissionAttempt))
        assert attempt.task_id == saved.provider_task_id == "journal-cloud-id"
        assert attempt.status == AttemptStatus.RUNNING
        assert attempt.credits_consumed == 7
        attempt.last_polled_at = None
        await session.commit()
        await restarted.poll_kling_tasks(session)
    adapter.submit.assert_not_awaited()
    adapter.query.assert_awaited_once_with("journal-cloud-id")


@pytest.mark.parametrize("verdict", ["REJECTED", "FAILED_BEFORE_SUBMIT"])
async def test_journal_proven_rejection_does_not_become_false_cloud_unknown(lab, verdict):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    await install_submission_migration(factory)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        command = await session.get(GenerationCommand, command_id)
        attempt.provider_account_id = "account-a"
        attempt.status = AttemptStatus.RECONCILING
        command.status = CommandStatus.UNCERTAIN
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint=attempt.intent_hash, status="STARTED",
        ))
        journal = KlingSubmissionReceipt(
            supervisor.settings.runner_root, f"{attempt_id.hex}:generation", attempt.provider,
            attempt.provider_account_id, attempt.intent_hash,
        )
        journal.begin()
        journal.finish(verdict, error_code="KLING_RATE_LIMITED")
        await session.commit()
        await supervisor.poll_kling_tasks(session)
        assert attempt.status == AttemptStatus.FAILED_BEFORE_SUBMIT
        assert attempt.charge_status == ChargeStatus.NOT_CHARGED
        assert attempt.last_error_code == "KLING_RATE_LIMITED"
        assert command.status == CommandStatus.TERMINAL
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt_id)
        )
        assert reservation.status == ReservationStatus.RELEASED
    adapter.submit.assert_not_awaited()
    adapter.query.assert_not_awaited()


async def test_local_error_cannot_release_a_previously_accepted_submission(lab):
    factory, supervisor, adapter = lab
    attempt_id, command_id = await ready(factory, supervisor)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt_id, sequence=1, provider=attempt.provider,
            idempotency_key=f"{attempt_id.hex}:generation",
            input_fingerprint=attempt.intent_hash, status="ACCEPTED",
            provider_task_id="previously-accepted",
        ))
        await session.commit()
    adapter.submission_account_id.side_effect = RuntimeError("synthetic local preparation error")
    await execute(factory, supervisor, command_id)
    async with factory() as session:
        attempt = await session.get(GenerationAttempt, attempt_id)
        receipt = await session.scalar(select(GenerationSubmissionAttempt))
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt_id)
        )
        assert attempt.status == AttemptStatus.RECONCILING
        assert reservation.status != ReservationStatus.RELEASED
        assert receipt.provider_task_id == "previously-accepted"
        assert receipt.status == "ACCEPTED"
    adapter.submit.assert_not_awaited()
