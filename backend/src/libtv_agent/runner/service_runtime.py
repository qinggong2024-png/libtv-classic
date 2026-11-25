import asyncio
import logging
import os
import platform
import socket
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from libtv_agent.adapters.kling_provider import create_kling_adapter
from libtv_agent.adapters.libtv import LibTVAdapter, LibTVError, create_libtv_adapter
from libtv_agent.adapters.object_store import create_object_store
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.enums import RunnerHostStatus
from libtv_agent.domain.models import AuditEvent, RunnerHost
from libtv_agent.runner.command_host_manager import CommandHostManager
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services.async_video_submission import (
    mark_interrupted_automatic_plans,
    mark_interrupted_direct_admissions,
    pending_automatic_video_ids,
    process_automatic_video_submission,
    release_stopped_automatic_plan,
)
from libtv_agent.services.bach_config import (
    bach_admin_config_signature,
    load_bach_admin_config,
)
from libtv_agent.services.confirmation import auto_confirm_pass_tasks
from libtv_agent.services.confirmation_recovery import (
    recover_stale_batch_confirmation_claims,
    recover_stale_confirmation_claims,
)
from libtv_agent.services.credentials import load_admin_credentials
from libtv_agent.services.durable_analysis import (
    recovery_loop as manual_analysis_recovery_loop,
)
from libtv_agent.services.durable_analysis import (
    worker_loop as manual_analysis_worker_loop,
)
from libtv_agent.services.durable_confirmation import (
    confirmation_worker_loop,
    reconcile_abandoned_confirmations,
)
from libtv_agent.services.idempotency import release_stale_idempotency_claims
from libtv_agent.services.kling_config import (
    kling_admin_config_signature,
    load_kling_admin_config,
)
from libtv_agent.services.observability import (
    operational_backlog_count,
    prune_operational_samples,
    record_operational_sample,
)
from libtv_agent.services.runtime_control import (
    get_runtime_config,
    set_libtv_auth_required,
    update_runtime_config,
)
from libtv_agent.services.wavespeed_config import (
    load_wavespeed_admin_config,
    wavespeed_admin_config_signature,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _video_provider_admin_config_signatures(
    settings: Settings,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, tuple[int, int] | None]:
    return (
        kling_admin_config_signature(settings),
        wavespeed_admin_config_signature(settings),
        bach_admin_config_signature(settings),
    )


def _load_video_provider_admin_configs(
    settings: Settings,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, tuple[int, int] | None]:
    load_kling_admin_config(settings)
    load_wavespeed_admin_config(settings)
    load_bach_admin_config(settings)
    return _video_provider_admin_config_signatures(settings)


def _admin_credentials_signature(path: Path) -> tuple[int, int] | None:
    """Track credential replacement without exposing its contents."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _refresh_ai_credentials(settings: Settings) -> None:
    """Load model secrets from the API's encrypted credential source.

    Rebuilding defaults also clears a removed stored key. Do not disturb
    existing video-provider or LibTV adapters during an AI credential update.
    """
    refreshed = Settings()
    refreshed.admin_credentials_file = settings.admin_credentials_file
    load_admin_credentials(refreshed)
    settings.openai_api_key = refreshed.openai_api_key
    settings.openai_base_url = refreshed.openai_base_url


def _atomic_service_instance_id(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path.read_text(encoding="ascii").strip()
    value = str(uuid.uuid4())
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(value, encoding="ascii")
    temporary.replace(path)
    return value


def _runner_heartbeat_due(
    host: RunnerHost, *, status: RunnerHostStatus, free_percent: float,
    observed_at: datetime, orphan_changed: bool = False,
) -> bool:
    """Ordinary health samples are not task ownership or paid receipts."""
    previous = host.last_heartbeat_at
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    return (
        previous is None
        or observed_at < previous
        or (observed_at - previous).total_seconds() >= 15
        or host.status != status
        or host.free_disk_percent is None
        or abs(host.free_disk_percent - free_percent) >= 1
        or orphan_changed
    )


async def _freeze_for_disk(session, settings: Settings, reason: str) -> None:
    current = await get_runtime_config(session, settings)
    if current is None:
        return
    config = dict(current.config_json)
    config["globalPaused"] = True
    config["paidSubmissionsPaused"] = True
    if config == current.config_json:
        return
    await update_runtime_config(
        session,
        settings,
        config=config,
        actor_id=None,
        reason=reason,
        expected_version=current.version,
    )


async def _refresh_libtv_auth_state(
    session,
    adapter: LibTVAdapter,
    settings: Settings,
) -> bool:
    if settings.libtv_adapter_mode != "real":
        return True
    try:
        await adapter.check_account()
    except LibTVError as exc:
        if exc.code != "LIBTV_AUTH_REQUIRED":
            logger.warning(
                "LibTV account health check failed without changing auth state: %s",
                exc.code,
            )
            current = await get_runtime_config(session, settings)
            return bool(current and not current.config_json.get("libtvAuthRequired", False))
        await set_libtv_auth_required(
            session,
            settings,
            required=True,
            reason="automatic pause: LibTV account health check requires login",
        )
        return False
    await set_libtv_auth_required(
        session,
        settings,
        required=False,
        reason="LibTV account health check recovered after administrator login",
    )
    return True


async def run_service_loop(stopping: Event, project_root: Path) -> None:
    os.chdir(project_root)
    settings = Settings()
    settings.validate_shared_environment()
    from libtv_agent.runner.classic_checkpoint import CheckpointStop
    from libtv_agent.services.classic_durable_config import (
        effective_runner_revision,
        matches_loaded,
        read_config,
    )

    durable_config = None
    if settings.classic_durable_config_file is not None:
        durable_config = read_config(settings.classic_durable_config_file)
        if not matches_loaded(settings, durable_config):
            raise RuntimeError("Runner durable configuration changed before startup")
        if durable_config["analysis"] or durable_config["confirmation"]:
            expected_runner = effective_runner_revision(
                settings.classic_durable_config_file.parent, durable_config
            )
            if (
                not isinstance(stopping, CheckpointStop)
                or os.environ.get("CLASSIC_DURABLE_RUNNER_SINGLETON") != "1"
                or stopping.identity["sourceRevision"] != expected_runner
                or settings.deployment_source_revision != expected_runner
            ):
                raise RuntimeError("Runner source or singleton proof does not match configuration")

    _refresh_ai_credentials(settings)
    admin_credentials_signature = _admin_credentials_signature(settings.admin_credentials_file)
    provider_config_signatures = _load_video_provider_admin_configs(settings)
    database = Database(settings.database_url)
    adapter = create_libtv_adapter(settings)
    kling_adapter = create_kling_adapter(settings)
    object_store = create_object_store(settings)
    manager = CommandHostManager(
        settings.runner_root / "command-hosts",
        project_root,
        settings.libtv_executable,
        execution_mode=settings.libtv_execution_mode,
        paid_generation_enabled=settings.paid_generation_enabled,
        paid_generation_confirmation=settings.paid_generation_confirmation,
        app_env=settings.app_env,
        libtv_config_dir=settings.libtv_config_dir,
    )
    manager.ensure_layout()
    service_instance_id = _atomic_service_instance_id(settings.runner_root / "service-instance-id")
    supervisor = RunnerSupervisor(
        adapter,
        object_store,
        settings,
        owner=service_instance_id,
        command_host_manager=manager,
        kling_adapter=kling_adapter,
        stop_claiming=stopping.is_set,
    )
    last_auth_check = 0.0
    last_metric_sample = 0.0
    last_metric_retention = 0.0
    last_idempotency_sweep = 0.0
    last_launch_recovery = 0.0
    last_parent_phase_reconciliation = 0.0
    last_orphan_signature: tuple[int, ...] = ()
    active_video_preparations: dict[uuid.UUID, tuple[asyncio.Task[None], uuid.UUID]] = {}
    last_plan_recovery = 0.0

    async def delivery_worker(worker_number: int) -> None:
        """Drain persisted downloads without blocking cloud polling or admission."""
        while not stopping.is_set():
            processed = 0
            try:
                # Do not start a large file download if the volume is full.
                disk = manager.disk_status(
                    settings.runner_log_disk_warning_percent,
                    settings.runner_log_disk_stop_percent,
                )
                if disk["status"] != "STOP":
                    async with database.session_factory() as delivery_session:
                        processed = await supervisor.resume_incomplete_downloads(
                            delivery_session, limit=1, recover_stale_only=True
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delivery worker failed", extra={"worker": worker_number})
            if not processed:
                await asyncio.sleep(max(1.0, settings.runner_poll_seconds))

    durable_analysis_tasks = []
    if settings.classic_durable_analysis_enabled:
        durable_analysis_tasks = [
            asyncio.create_task(
                manual_analysis_worker_loop(
                    database, settings, object_store, kling_adapter, stopping
                ),
                name=f"manual-analysis-{number}",
            )
            for number in range(settings.classic_durable_analysis_concurrency)
        ]
        durable_analysis_tasks.append(
            asyncio.create_task(
                manual_analysis_recovery_loop(database, settings, stopping),
                name="manual-analysis-recovery",
            )
        )
    if settings.classic_durable_confirmation_enabled:
        # Production entry is classic_runner.py, which holds the OS lifetime lock.
        # Explicitly require that launcher proof before reconciling orphaned jobs.
        if os.environ.get("CLASSIC_DURABLE_RUNNER_SINGLETON") == "1":
            await reconcile_abandoned_confirmations(database)
        durable_analysis_tasks.append(
            asyncio.create_task(
                confirmation_worker_loop(
                    database, settings, adapter, object_store, lambda: kling_adapter, stopping
                ),
                name="durable-confirmation",
            )
        )
    delivery_tasks = [
        asyncio.create_task(delivery_worker(number), name=f"classic-delivery-{number}")
        for number in (
            range(settings.download_concurrency)
            if settings.classic_throughput_s1s3_enabled
            else ()
        )
    ]
    readiness_task = None
    if durable_config and (durable_config["analysis"] or durable_config["confirmation"]):
        from libtv_agent.runner.classic_durable_readiness import publish_readiness

        readiness_task = asyncio.create_task(
            publish_readiness(database, settings, stopping, durable_analysis_tasks),
            name="durable-readiness",
        )
    clean_stop = False
    try:
        while not stopping.is_set():
            try:
                current_admin_credentials_signature = _admin_credentials_signature(
                    settings.admin_credentials_file
                )
                if current_admin_credentials_signature != admin_credentials_signature:
                    _refresh_ai_credentials(settings)
                    admin_credentials_signature = current_admin_credentials_signature
                current_provider_config_signatures = _video_provider_admin_config_signatures(
                    settings
                )
                if current_provider_config_signatures != provider_config_signatures:
                    provider_config_signatures = _load_video_provider_admin_configs(settings)
                    kling_adapter = create_kling_adapter(settings)
                    supervisor.replace_kling_adapter(kling_adapter)
                pending_video_ids: list[uuid.UUID] = []
                disk = manager.disk_status(
                    settings.runner_log_disk_warning_percent,
                    settings.runner_log_disk_stop_percent,
                )
                orphans = manager.find_orphan_processes()
                async with database.session_factory() as session:
                    now = time.monotonic()
                    if now - last_auth_check >= settings.libtv_auth_check_seconds:
                        await _refresh_libtv_auth_state(session, adapter, settings)
                        last_auth_check = now
                    host = await session.scalar(
                        select(RunnerHost).where(
                            RunnerHost.service_instance_id == service_instance_id
                        )
                    )
                    status = {
                        "OK": RunnerHostStatus.ONLINE,
                        "WARNING": RunnerHostStatus.WARNING,
                        "STOP": RunnerHostStatus.PAUSED,
                    }[disk["status"]]
                    new_host = host is None
                    if new_host:
                        host = RunnerHost(
                            hostname=socket.gethostname(),
                            os_version=platform.platform(),
                            protocol_version=1,
                            service_version="0.2.0",
                            service_instance_id=service_instance_id,
                        )
                        session.add(host)
                    observed_at = datetime.now(UTC)
                    free_percent = float(disk["freePercent"])
                    orphan_signature = tuple(sorted(orphans))
                    orphan_changed = orphan_signature != last_orphan_signature
                    if orphan_signature and status != RunnerHostStatus.PAUSED:
                        status = RunnerHostStatus.WARNING
                    if new_host or _runner_heartbeat_due(
                        host, status=status, free_percent=free_percent,
                        observed_at=observed_at, orphan_changed=orphan_changed,
                    ):
                        host.status = status
                        host.last_heartbeat_at = observed_at
                        host.free_disk_percent = free_percent
                    if orphan_signature and orphan_changed:
                        session.add(
                            AuditEvent(
                                actor_id=None,
                                action="RUNNER_ORPHAN_DETECTED",
                                entity_type="runner_host",
                                entity_id=service_instance_id,
                                reason=f"{len(orphans)} unowned command-host process(es)",
                                correlation_id=str(uuid.uuid4()),
                                evidence_refs_json=[],
                            )
                        )
                    # End the transaction before provider/network work, even
                    # on loops where the health sample required no write.
                    await session.commit()
                    last_orphan_signature = orphan_signature
                    if disk["status"] == "STOP":
                        backlog_count = await operational_backlog_count(session)
                        await _freeze_for_disk(
                            session,
                            settings,
                            "automatic stop: runner log volume below disk threshold",
                        )
                        await supervisor.observe_command_hosts(session)
                    else:
                        runtime = await get_runtime_config(session, settings)
                        global_limit = int(
                            (runtime.config_json if runtime else {})
                            .get("generationConcurrency", {})
                            .get("global", 1)
                        )
                        if not stopping.is_set():
                            await auto_confirm_pass_tasks(
                                session,
                                adapter,
                                object_store,
                                settings,
                                kling_adapter=kling_adapter,
                                limit=global_limit,
                                stop_claiming=stopping.is_set,
                            )
                        backlog_count = await operational_backlog_count(session)
                        # Commit any auto-confirm writes and queue-snapshot read
                        # transaction before running the potentially slow
                        # provider/remote observation cycle. A still-open
                        # SQLite transaction can block both API and CUT writes.
                        # This is a transaction boundary, NOT a provider retry.
                        await session.commit()
                        await supervisor.run_ready(session)
                        pending_video_ids = await pending_automatic_video_ids(
                            session, limit=settings.classic_auto_plan_concurrency
                        )
                    if now - last_plan_recovery >= 60:
                        interrupted = await mark_interrupted_automatic_plans(
                            session, exclude_ids=tuple(active_video_preparations)
                        )
                        if interrupted:
                            logger.warning(
                                "reconciled abandoned automatic plans",
                                extra={"count": interrupted},
                            )
                        abandoned_admissions = await mark_interrupted_direct_admissions(
                            session, exclude_ids=tuple(active_video_preparations),
                        )
                        if abandoned_admissions:
                            logger.warning(
                                "identified interrupted pre-paid direct admissions",
                                extra={"count": abandoned_admissions},
                            )
                        last_plan_recovery = now
                    if now - last_parent_phase_reconciliation >= 60:
                        # A validated last download can survive an interrupted parent
                        # aggregate commit. Correct it without another provider call.
                        reconciled = await supervisor.reconcile_completed_parent_phases(session)
                        if reconciled:
                            logger.warning(
                                "repaired fully delivered parents stuck in GENERATING",
                                extra={"count": reconciled},
                            )
                        last_parent_phase_reconciliation = now
                    if now - last_launch_recovery >= 60:
                        # Sits outside the STOP branch on purpose: nothing here submits new
                        # work, it only resolves commands the previous runner abandoned at
                        # LAUNCH_STARTED. Those keep occupying uq_attempt_active_intent, so
                        # the affected task cannot be retried until they are settled.
                        recovered = await supervisor.recover_stale_launch_started_commands(
                            session
                        )
                        if recovered:
                            logger.warning(
                                "resolved commands abandoned between LAUNCH_STARTED and "
                                "PROCESS_STARTED",
                                extra={"count": recovered},
                            )
                        last_launch_recovery = now
                    if now - last_metric_sample >= settings.runner_metric_sample_seconds:
                        await record_operational_sample(
                            session,
                            settings,
                            host,
                            backlog_count=backlog_count,
                            orphan_process_count=len(orphans),
                            free_disk_percent=float(disk["freePercent"]),
                        )
                        last_metric_sample = now
                    if now - last_metric_retention >= 24 * 60 * 60:
                        await prune_operational_samples(
                            session, settings.runner_metric_retention_days
                        )
                        last_metric_retention = now
                    if now - last_idempotency_sweep >= 5 * 60:
                        # Confirmation work is handed to a background job that outlives
                        # the HTTP request owning the idempotency claim. A request that
                        # dies before settling leaves the key at response_status=0, and
                        # every replay then answers 409 IDEMPOTENCY_REQUEST_IN_PROGRESS
                        # for ever. Nothing else in the system ever looks at those rows.
                        confirmation_recovery = await recover_stale_confirmation_claims(
                            session, settings
                        )
                        batch_confirmation_recovery = (
                            await recover_stale_batch_confirmation_claims(session, settings)
                        )
                        released = await release_stale_idempotency_claims(session)
                        if (
                            released
                            or any(confirmation_recovery.values())
                            or any(batch_confirmation_recovery.values())
                        ):
                            logger.warning(
                                "recovered stale idempotency claims",
                                extra={
                                    "released": released,
                                    "confirmation_recovery": confirmation_recovery,
                                    "batch_confirmation_recovery": batch_confirmation_recovery,
                                },
                            )
                        last_idempotency_sweep = now
                    await session.commit()
                # Never hold the runner's database transaction during a remote AI call.
                for video_id in pending_video_ids:
                    if stopping.is_set():
                        break
                    if (
                        video_id in active_video_preparations
                        or len(active_video_preparations) >= settings.classic_auto_plan_concurrency
                    ):
                        continue
                    run_id = uuid.uuid4()
                    worker = asyncio.create_task(process_automatic_video_submission(
                        database, settings, kling_adapter, object_store, video_id,
                        run_id=run_id,
                    ))
                    active_video_preparations[video_id] = (worker, run_id)
                    worker.add_done_callback(
                        lambda finished, identifier=video_id: active_video_preparations.pop(
                            identifier, None
                        )
                    )
            except Exception:
                logger.exception("runner service cycle failed")
            await asyncio.to_thread(stopping.wait, settings.runner_poll_seconds)
        clean_stop = True
    finally:
        if readiness_task is not None:
            readiness_task.cancel()
            await asyncio.gather(readiness_task, return_exceptions=True)
        if isinstance(stopping, CheckpointStop) and clean_stop and stopping.is_set():
            stopping.publish("DRAINING")
            # No cancellation of in-flight paid preparation or file writes.
            # Idle polling sleeps may delay exit; a stuck operation blocks the
            # upgrade rather than causing an ambiguous replay.
            jobs = [entry[0] for entry in active_video_preparations.values()]
            results = await asyncio.gather(
                *jobs, *durable_analysis_tasks, *delivery_tasks, return_exceptions=True
            )
            await database.dispose()
            if any(isinstance(result, BaseException) for result in results):
                stopping.publish("DRAIN_FAILED")
                raise RuntimeError("worker checkpoint contains an unhandled operation failure")
            stopping.publish("DRAINED")
        else:
            if active_video_preparations:
                # Join workers before requeueing their CAS-protected pre-paid leases.
                stopping_workers = list(active_video_preparations.items())
                for pending_worker, _ in (entry for _, entry in stopping_workers):
                    pending_worker.cancel()
                await asyncio.gather(
                    *(entry[0] for _, entry in stopping_workers), return_exceptions=True,
                )
                try:
                    async with database.session_factory() as session:
                        for video_id, (_, run_id) in stopping_workers:
                            await release_stopped_automatic_plan(
                                session, task_id=video_id, run_id=run_id,
                            )
                except Exception:
                    # The next runner will recover expired leases via durable state.
                    logger.exception("could not release cancelled Sol planning leases")
            for worker in durable_analysis_tasks:
                worker.cancel()
            await asyncio.gather(*durable_analysis_tasks, return_exceptions=True)
            for worker in delivery_tasks:
                worker.cancel()
            await asyncio.gather(*delivery_tasks, return_exceptions=True)
            await database.dispose()
