import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from libtv_agent.adapters.kling_official_cli import (
    KlingCliError,
    map_kling_status,
    retry_kling_call,
)
from libtv_agent.adapters.kling_provider import (
    create_kling_adapter,
    create_kling_adapter_for_provider,
)
from libtv_agent.adapters.kling_submission_receipts import KlingSubmissionReceipt
from libtv_agent.adapters.libtv import GenerationResult, LibTVAdapter, LibTVError
from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.video_provider import (
    ProviderSubmissionMatch,
    ProviderSubmissionResult,
    VideoElementReference,
    VideoGenerationRequest,
    VideoProviderAdapter,
)
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import delivery_filename
from libtv_agent.domain.enums import (
    AttemptStatus,
    BillingSource,
    ChargeStatus,
    CommandStatus,
    DownloadStatus,
    GenerationStatus,
    ParentTaskPhase,
    ValidationStatus,
)
from libtv_agent.domain.models import (
    Asset,
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    GenerationVariant,
    ParentTask,
    PointsReservation,
    ValidationResult,
)
from libtv_agent.runner.command_host_manager import CommandHostManager
from libtv_agent.runner.deep_video_pipeline import (
    pipeline_request,
    pipeline_state,
    queue_replacement,
    record_pipeline_submission,
)
from libtv_agent.services.capacity import acquire_capacity, release_capacity, renew_capacity
from libtv_agent.services.generation_safety import (
    PaidGenerationBlockedError,
    real_provider_submission_authorized,
    require_paid_generation_authorized,
)
from libtv_agent.services.kling_risk_control import (
    KLING_RISK_CONTROL_ERROR,
)
from libtv_agent.services.kling_risk_control import (
    kling_risk_control_rejected as _kling_risk_control_rejected,
)
from libtv_agent.services.media import (
    download_with_retries,
    technical_validate,
    technical_validation_options,
)
from libtv_agent.services.points import business_date, settle_points
from libtv_agent.services.provider_errors import clear_provider_error, record_provider_error
from libtv_agent.services.provider_routing import ProviderKind, provider_kind
from libtv_agent.services.runtime_control import (
    provider_claims_allowed,
    set_libtv_auth_required,
)
from libtv_agent.services.state_machine import aggregate_parent_phase
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)








def _provider_receipt_confirms_zero_billing(payload: dict) -> bool:
    """Only a nonempty, entirely zero-valued task billing receipt is conclusive.

    Kling may omit charge_type on failed task receipts. Do not infer zero from
    missing billing, an empty list, malformed values or partial mixed charges.
    """
    billing = payload.get("billing")
    if not isinstance(billing, list) or not billing:
        return False
    for item in billing:
        if not isinstance(item, dict) or "amount" not in item or isinstance(item["amount"], bool):
            return False
        try:
            amount = Decimal(str(item["amount"]))
        except (InvalidOperation, TypeError, ValueError):
            return False
        if not amount.is_finite() or amount != 0:
            return False
    return True


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


_MAX_KLING_RECONCILIATION_ATTEMPTS = 5
_KLING_RECONCILIATION_BASE_SECONDS = 5.0
_KLING_RECONCILIATION_MAX_SECONDS = 5 * 60.0


def _kling_asset_suffix(filename: str, object_key: str | None) -> str:
    for candidate in (object_key, filename):
        suffix = Path(candidate or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png"}:
            return suffix
    return ".jpg"


def _video_execution_backend(provider: str, settings: Settings) -> str:
    normalized = provider.strip().lower()
    if normalized == settings.wavespeed_video_provider_name.strip().lower():
        return "WAVESPEED_VIDEO_API"
    if normalized == settings.bach_provider_name.strip().lower():
        return "BACH_API"
    if normalized in {"kling", settings.kling_api_provider_name.strip().lower()}:
        return "KLING_OFFICIAL_API"
    return "KLING_OFFICIAL_CLI"


def _video_timeout_error(provider: str, settings: Settings) -> tuple[str, str]:
    if provider.strip().lower() == settings.kling_cli_provider_name.strip().lower():
        return (
            "KLING_CLI_TASK_TIMEOUT",
            "Kling CLI task exceeded the configured wait time; continuing read-only reconciliation",
        )
    if provider.strip().lower() == settings.bach_provider_name.strip().lower():
        return (
            "BACH_TASK_TIMEOUT",
            "Bach video task exceeded the configured wait time; "
            "the remote task was not resubmitted",
        )
    if provider.strip().lower() == settings.wavespeed_video_provider_name.strip().lower():
        return (
            "WAVESPEED_VIDEO_TASK_TIMEOUT",
            "WaveSpeed video task exceeded the configured wait time; "
            "the remote task was not resubmitted",
        )
    return (
        "KLING_API_TASK_TIMEOUT",
        "Kling API task exceeded the configured wait time; the remote task was not resubmitted",
    )


@dataclass(frozen=True)
class ProcessIdentity:
    command_host_id: str
    process_id: int
    process_started_at: str
    nonce: str
    executable_hash: str
    command_hash: str


def process_identity_matches(expected: ProcessIdentity, observed: ProcessIdentity) -> bool:
    return expected == observed


def classify_recovery(
    *,
    launch_started: bool,
    command_host_alive: bool,
    identity_matches: bool,
    terminal_result_valid: bool,
    host_restarted: bool,
    log_writable: bool = True,
) -> str:
    if terminal_result_valid:
        return "TERMINAL"
    if host_restarted and launch_started:
        return "RECOVERY_AUDIT"
    if command_host_alive and identity_matches:
        return "RUNNING"
    if command_host_alive and not identity_matches:
        return "MANUAL_REVIEW"
    if launch_started or not log_writable:
        return "UNKNOWN"
    return "READY"


# Written when aggregation drives a task into ACTION_REQUIRED and no business layer
# recorded a cause first.
AGGREGATED_ACTION_REQUIRED = "AGGREGATED_ACTION_REQUIRED"

# Only these reason codes unlock confirmation.confirm_task's materialization recovery
# path (it tests `phase_reason_code.startswith("MATERIALIZATION_")`).
RECOVERY_GATING_REASON_PREFIX = "MATERIALIZATION_"

# A command sitting at LAUNCH_STARTED past its lease is only treated as abandoned after
# this extra grace period. The lease is renewed by the launching attempt, so a process
# that is merely slow to reach PROCESS_STARTED still holds a live lease and is skipped.
STALE_LAUNCH_GRACE = timedelta(minutes=15)


def reason_code_for_aggregate(
    current_code: str | None,
    previous_phase: ParentTaskPhase,
    next_phase: ParentTaskPhase,
) -> str | None:
    """Keep phase_reason_code consistent with the phase it describes.

    Two failures are being prevented here.

    1. Aggregation used to leave phase_reason_code untouched, so a task driven into
       ACTION_REQUIRED by a variant failure kept a NULL code. confirm_task requires a
       non-empty code before it even considers recovery, so those tasks were
       unrecoverable and the field told the operator nothing.

    2. Clearing unconditionally would be worse than not clearing at all: a task that
       recovers from a materialization failure, later fails again for an unrelated
       variant reason, and lands back in ACTION_REQUIRED would still advertise
       MATERIALIZATION_* recovery. Pressing it re-runs materialization and reserves
       points again. So only the codes that actually gate recovery are cleared, and
       only on the way out of ACTION_REQUIRED.

    Codes that do not gate recovery are preserved because the read model consumes
    them: services.tasks._provider_for_read reads the BACH_ / WAVESPEED_ prefixes to
    attribute a Kling task to the sub-provider that produced it.
    """
    if next_phase == ParentTaskPhase.ACTION_REQUIRED:
        return current_code or AGGREGATED_ACTION_REQUIRED
    if next_phase == ParentTaskPhase.DELIVERABLE and current_code == AGGREGATED_ACTION_REQUIRED:
        # The generic aggregation failure is resolved when every output is ready.
        # Keep provider attribution codes such as BACH_* and WAVESPEED_* intact.
        return None
    if previous_phase == ParentTaskPhase.ACTION_REQUIRED and (current_code or "").startswith(
        RECOVERY_GATING_REASON_PREFIX
    ):
        return None
    return current_code


class RunnerSupervisor:
    def __init__(
        self,
        adapter: LibTVAdapter,
        object_store: ObjectStore,
        settings: Settings,
        owner: str = "phase-one-runner",
        command_host_manager: CommandHostManager | None = None,
        kling_adapter: VideoProviderAdapter | None = None,
    ) -> None:
        self.adapter = adapter
        self.object_store = object_store
        self.settings = settings
        self.owner = owner
        self._download_semaphore = asyncio.Semaphore(settings.download_concurrency)
        self._validation_semaphore = asyncio.Semaphore(settings.technical_validation_concurrency)
        self.kling_adapter = kling_adapter or create_kling_adapter(settings)
        self._kling_adapters: dict[str, VideoProviderAdapter] = {
            self.kling_adapter.get_provider_name().strip().lower(): self.kling_adapter
        }
        self.command_host_manager = command_host_manager or CommandHostManager(
            settings.runner_root / "command-hosts",
            Path(__file__).resolve().parents[4],
            settings.libtv_executable,
            execution_mode=settings.libtv_execution_mode,
            paid_generation_enabled=settings.paid_generation_enabled,
            paid_generation_confirmation=settings.paid_generation_confirmation,
            app_env=settings.app_env,
            libtv_config_dir=settings.libtv_config_dir,
        )

    def replace_kling_adapter(self, adapter: VideoProviderAdapter) -> None:
        self.kling_adapter = adapter
        # Config reloads must also invalidate inactive adapters used to recover
        # persisted tasks; they are recreated from the task's provider name.
        self._kling_adapters = {
            adapter.get_provider_name().strip().lower(): adapter,
        }

    def _kling_adapter_for(self, provider: str) -> VideoProviderAdapter:
        normalized = provider.strip().lower()
        adapter = self._kling_adapters.get(normalized)
        if adapter is None:
            adapter = create_kling_adapter_for_provider(self.settings, provider)
            self._kling_adapters[normalized] = adapter
        return adapter

    async def _require_original_api_account(
        self, attempt: GenerationAttempt, adapter: VideoProviderAdapter,
    ) -> None:
        """Original-account fence for read-only official API recovery."""
        if attempt.provider != self.settings.kling_api_provider_name:
            return
        reader = getattr(adapter, "submission_account_id", None)
        if not attempt.provider_account_id or not callable(reader):
            raise KlingCliError(
                "KLING_RECONCILIATION_ACCOUNT_UNVERIFIED",
                "Original submission account cannot be verified",
            )
        if await reader() != attempt.provider_account_id:
            raise KlingCliError(
                "KLING_ACCOUNT_CHANGED",
                "Current account differs from original paid submission account",
            )

    async def _release_capacity_if_charge_classified(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
    ) -> bool:
        if attempt.charge_status == ChargeStatus.UNKNOWN:
            return False
        await release_capacity(session, command.id)
        return True

    async def _kling_api_at_cloud_capacity(self, session: AsyncSession) -> bool:
        """Gate outstanding remote jobs, not just simultaneous HTTP POST calls."""
        adapter = self._kling_adapter_for(self.settings.kling_api_provider_name)
        account_reader = getattr(adapter, "submission_account_id", None)
        try:
            account_id = await account_reader() if callable(account_reader) else None
        except KlingCliError:
            # Missing/invalid credentials get their normal explicit submit error.
            return False
        active = select(func.count(GenerationAttempt.id)).where(
            # "kling" is the legacy official-API provider alias.
            GenerationAttempt.provider.in_(
                (self.settings.kling_api_provider_name, "kling")
            ),
            or_(
                and_(
                    GenerationAttempt.task_id.is_not(None),
                    GenerationAttempt.task_id != "",
                    GenerationAttempt.status.in_(
                        (AttemptStatus.RUNNING, AttemptStatus.RECONCILING,
                         AttemptStatus.UNKNOWN_AFTER_SUBMIT)
                    ),
                ),
                # A durable submit invocation with an unknown handle may still
                # have consumed cloud capacity. Never silently reuse its slot.
                and_(
                    GenerationAttempt.status.in_(
                        (AttemptStatus.RECONCILING, AttemptStatus.UNKNOWN_AFTER_SUBMIT)
                    ),
                    GenerationAttempt.submission_started_at.is_not(None),
                ),
            ),
        )
        if account_id:
            active = active.where(or_(
                GenerationAttempt.provider_account_id == account_id,
                GenerationAttempt.provider_account_id.is_(None),
            ))
        count = int(await session.scalar(active) or 0)
        return count >= self.settings.kling_api_max_active_cloud_tasks

    async def run_ready(self, session: AsyncSession) -> int:
        from libtv_agent.runner.launch_recovery import recover_abandoned_claims

        await recover_abandoned_claims(session)
        processed = 0
        if not self.settings.classic_throughput_s1s3_enabled:
            # Preserve the established Runner contract until the new workload
            # has passed production-shaped SQLite and 20-slot safety gates.
            processed += await self.resume_incomplete_downloads(session)
            await self.observe_command_hosts(session)
            processed += await self.poll_kling_tasks(session)
            processed += await self._run_ready_provider(session, "libtv")
            processed += await self._run_ready_provider(session, "kling")
            return processed
        # Delivery is drained by independent workers. Never put large downloads
        # ahead of poll/READY admission in the submission runner.
        await self.observe_command_hosts(session)
        # Fill vacant cloud slots before potentially slow status queries, then
        # fill slots freed by terminal observations in this same cycle.
        # Each READY claim and paid submission remains durable and idempotent.
        processed += await self._run_ready_provider(session, "kling")
        processed += await self.poll_kling_tasks(session)
        processed += await self._run_ready_provider(session, "kling")
        # The in-process LibTV queue is independent of Kling cloud admission.
        processed += await self._run_ready_provider(session, "libtv")
        return processed

    async def _run_ready_provider(
        self, session: AsyncSession, expected_provider: ProviderKind
    ) -> int:
        processed = 0
        provider_names = (
            ("libtv",) if expected_provider == "libtv" else self.settings.kling_provider_names
        )
        while True:
            commands = list(
                await session.scalars(
                    select(GenerationCommand)
                    .join(GenerationAttempt, GenerationAttempt.id == GenerationCommand.attempt_id)
                    .where(
                        GenerationCommand.status == CommandStatus.READY,
                        GenerationAttempt.provider.in_(provider_names),
                    )
                    # Include delayed READY rows when determining FIFO head.
                    # Excluding them here lets younger tasks bypass a rejected 429.
                    .order_by(GenerationCommand.created_at, GenerationCommand.id)
                    .with_for_update(skip_locked=True)
                    .limit(20)
                )
            )
            command = None
            api_front_blocked = False
            for candidate in commands:
                candidate_attempt = await session.get(GenerationAttempt, candidate.attempt_id)
                candidate_variant = (
                    await session.get(GenerationVariant, candidate_attempt.variant_id)
                    if candidate_attempt
                    else None
                )
                candidate_task = (
                    await session.get(ParentTask, candidate_variant.parent_task_id)
                    if candidate_variant
                    else None
                )
                if (
                    candidate_attempt
                    and candidate_task
                    and provider_kind(candidate_attempt.provider, self.settings)
                    == expected_provider
                    and await provider_claims_allowed(
                        session,
                        self.settings,
                        candidate_attempt.provider,
                        user_id=candidate_task.user_id,
                        workflow_kind=candidate_task.workflow_kind,
                        provider_model=candidate_attempt.provider_model,
                    )
                ):
                    # A deferred API head keeps its place; later API jobs cannot
                    # bypass it. Other providers still have independent queues.
                    if candidate_attempt.provider == self.settings.kling_api_provider_name:
                        if api_front_blocked:
                            continue
                        if (
                            candidate.lease_expires_at is not None
                            and _utc(candidate.lease_expires_at) > datetime.now(UTC)
                        ):
                            api_front_blocked = True
                            continue
                        if await self._kling_api_at_cloud_capacity(session):
                            if candidate_attempt.provider_status != "waiting_capacity":
                                candidate_attempt.provider_status = "waiting_capacity"
                                await session.commit()
                            api_front_blocked = True
                            continue
                    elif (
                        candidate.lease_expires_at is not None
                        and _utc(candidate.lease_expires_at) > datetime.now(UTC)
                    ):
                        continue
                    command = candidate
                    break
            if command is None:
                break
            lease = await acquire_capacity(session, self.settings, command)
            if lease is None:
                await session.rollback()
                break
            command.status = CommandStatus.CLAIMED
            command.claim_owner = self.owner
            command.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            attempt = await session.get(GenerationAttempt, command.attempt_id)
            if attempt is None or attempt.status != AttemptStatus.DISPATCH_PENDING:
                command.status = CommandStatus.CANCELLED
                await release_capacity(session, command.id)
                await session.commit()
                continue
            attempt.status = AttemptStatus.LAUNCHING
            attempt.state_version += 1
            await session.commit()
            terminal = await self._execute(session, command.id)
            if terminal is None:
                continue
            if expected_provider == "kling":
                await release_capacity(session, command.id)
                await session.commit()
            elif terminal:
                await self._release_capacity_if_charge_classified(session, command, attempt)
                await session.commit()
            processed += 1
        return processed

    async def resume_incomplete_downloads(
        self,
        session: AsyncSession,
        *,
        limit: int = 10,
        recover_stale_only: bool = False,
    ) -> int:
        # The durable variant status is the download outbox. Workers claim
        # QUEUED rows atomically, and reclaim only stale abandoned downloads.
        stale_cutoff = datetime.now(UTC) - timedelta(minutes=15)
        latest_attempt = (
            select(GenerationAttempt.id)
            .where(GenerationAttempt.variant_id == GenerationVariant.id)
            .order_by(GenerationAttempt.created_at.desc(), GenerationAttempt.id.desc())
            .limit(1)
            .correlate(GenerationVariant)
            .scalar_subquery()
        )
        variants = list(
            await session.scalars(
                select(GenerationVariant)
                .join(GenerationAttempt, GenerationAttempt.variant_id == GenerationVariant.id)
                .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
                .where(
                    ParentTask.phase != ParentTaskPhase.CANCELLED,
                    GenerationAttempt.id == latest_attempt,
                    or_(
                        GenerationVariant.download_status == DownloadStatus.QUEUED,
                        and_(
                            GenerationVariant.download_status == DownloadStatus.DOWNLOADING,
                            GenerationAttempt.updated_at <= stale_cutoff,
                        ) if recover_stale_only else
                        GenerationVariant.download_status == DownloadStatus.DOWNLOADING,
                        and_(
                            GenerationVariant.download_status == DownloadStatus.FAILED,
                            GenerationAttempt.status == AttemptStatus.SUCCEEDED,
                            GenerationAttempt.last_error_code == "DOWNLOAD_RETRYABLE",
                            GenerationAttempt.updated_at
                            <= datetime.now(UTC) - timedelta(seconds=60),
                        ),
                    ),
                )
                .order_by(GenerationVariant.created_at)
                .limit(limit)
            )
        )
        if not recover_stale_only:
            for variant in variants:
                await self._aggregate_task(session, variant.id)

        resumed = 0
        for variant in variants:
            attempt = await session.scalar(
                select(GenerationAttempt)
                .where(GenerationAttempt.variant_id == variant.id)
                .order_by(GenerationAttempt.created_at.desc(), GenerationAttempt.id.desc())
                .limit(1)
            )
            if attempt is None:
                continue
            if recover_stale_only:
                if variant.download_status == DownloadStatus.DOWNLOADING:
                    claim = await session.execute(
                        update(GenerationAttempt)
                        .where(
                            GenerationAttempt.id == attempt.id,
                            GenerationAttempt.updated_at <= stale_cutoff,
                        )
                        .values(updated_at=datetime.now(UTC))
                        .execution_options(synchronize_session=False)
                    )
                else:
                    claim = await session.execute(
                        update(GenerationVariant)
                        .where(
                            GenerationVariant.id == variant.id,
                            GenerationVariant.download_status == variant.download_status,
                        )
                        .values(download_status=DownloadStatus.DOWNLOADING)
                        .execution_options(synchronize_session=False)
                    )
                    if claim.rowcount == 1:
                        attempt.updated_at = datetime.now(UTC)
                if claim.rowcount != 1:
                    await session.rollback()
                    continue
                await session.commit()  # release claim transaction before CDN query
            command = await session.scalar(
                select(GenerationCommand).where(GenerationCommand.attempt_id == attempt.id)
            )
            if command is None:
                continue
            payload = dict(attempt.raw_response_json or {})
            download_adapter = (
                self._kling_adapter_for(attempt.provider) if attempt.provider != "libtv" else None
            )
            if (
                variant.download_status == DownloadStatus.FAILED
                and download_adapter is not None
                and attempt.task_id
            ):
                # CDN URLs can expire while delivery is unavailable. Query the
                # original paid handle for a fresh URL; never submit a replacement.
                try:
                    await self._require_original_api_account(attempt, download_adapter)
                    refreshed = await download_adapter.query(attempt.task_id)
                    if (
                        refreshed.status not in {"succeeded", "partial"}
                        or not refreshed.outputs
                        or not refreshed.outputs[0].url
                    ):
                        raise ValueError("delivery output is not available yet")
                    payload["videoUrl"] = refreshed.outputs[0].url
                    attempt.output_url = refreshed.outputs[0].url
                except Exception:
                    record_provider_error(
                        attempt,
                        code="DOWNLOAD_RETRYABLE",
                        message="Delivery source refresh failed; the original task will be queried",
                        retryable=True,
                    )
                    attempt.updated_at = datetime.now(UTC)
                    await session.commit()
                    continue
            output_url = str(payload.get("videoUrl") or attempt.output_url or "")
            if not output_url:
                variant.download_status = DownloadStatus.FAILED
                variant.validation_status = ValidationStatus.FAILED
                attempt.last_error_code = "DOWNLOAD_SOURCE_MISSING"
                await session.commit()
                await self._aggregate_task(session, variant.id)
                resumed += 1
                continue
            payload["videoUrl"] = output_url
            await self._handle_success(
                session,
                command,
                attempt,
                GenerationResult(status="SUCCEEDED", payload=payload),
                download_adapter=download_adapter,
                generation_status=(
                    variant.generation_status
                    if variant.generation_status
                    in {
                        GenerationStatus.SUCCEEDED,
                        GenerationStatus.PARTIAL,
                    }
                    else GenerationStatus.SUCCEEDED
                ),
            )
            resumed += 1
        return resumed

    async def reconcile_attempt(self, session: AsyncSession, attempt_id: uuid.UUID) -> str:
        attempt = await session.get(GenerationAttempt, attempt_id)
        if attempt is None or attempt.status != AttemptStatus.UNKNOWN_AFTER_SUBMIT:
            raise ValueError("attempt is not UNKNOWN_AFTER_SUBMIT")
        command = await session.scalar(
            select(GenerationCommand).where(GenerationCommand.attempt_id == attempt.id)
        )
        if command is None:
            raise ValueError("generation command is missing")
        if attempt.provider != "libtv":
            kling_adapter = self._kling_adapter_for(attempt.provider)
            await self._require_original_api_account(attempt, kling_adapter)
            if not attempt.task_id:
                await self._recover_history_handle(
                    session, attempt, kling_adapter
                )
            provider_result = await kling_adapter.query(attempt.task_id)
            if provider_result.credits_consumed is not None:
                attempt.credits_consumed = provider_result.credits_consumed
            result = GenerationResult(
                status=provider_result.status.upper(),
                payload={
                    **provider_result.raw_response,
                    "taskId": attempt.task_id,
                    "videoUrl": provider_result.outputs[0].url if provider_result.outputs else None,
                },
            )
        else:
            result = await self.adapter.query_node_result(
                project_uuid=attempt.project_uuid or "",
                node_key=attempt.video_node_key or "",
            )
        if (
            attempt.provider == self.settings.kling_api_provider_name
            and attempt.task_id
            and result.status in {"RUNNING", "QUEUED", "PENDING", "PROCESSING"}
        ):
            # A read-only check of the ORIGINAL cloud ID on the ORIGINAL
            # account proves acceptance. Never create another paid command.
            claimed = await session.execute(
                update(GenerationAttempt).where(
                    GenerationAttempt.id == attempt.id,
                    GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT,
                    GenerationAttempt.state_version == attempt.state_version,
                ).values(
                    status=AttemptStatus.RUNNING,
                    provider_status=result.status.lower(),
                    next_reconciliation_at=None,
                    last_polled_at=datetime.now(UTC),
                    state_version=GenerationAttempt.state_version + 1,
                ).execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                await session.rollback()
                return "RECONCILING"
            if command.status == CommandStatus.UNCERTAIN:
                command.status = CommandStatus.PROCESS_STARTED
                command.process_started_at = (
                    command.process_started_at or datetime.now(UTC)
                )
                command.lease_expires_at = None
            await session.commit()
            return "RUNNING"
        if result.status in {"SUCCEEDED", "PARTIAL"}:
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if variant is not None and pipeline_state(command).get("stage") == "depth":
                claimed = await session.execute(
                    update(GenerationAttempt)
                    .where(
                        GenerationAttempt.id == attempt.id,
                        GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT,
                        GenerationAttempt.state_version == attempt.state_version,
                    )
                    .values(state_version=GenerationAttempt.state_version + 1)
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount != 1:
                    await session.rollback()
                    return "RUNNING"
                task = await session.get(ParentTask, variant.parent_task_id)
                await queue_replacement(
                    session,
                    command,
                    attempt,
                    variant,
                    task,
                    provider_result,
                )
                return "RUNNING"
            await self._handle_success(
                session,
                command,
                attempt,
                result,
                download_adapter=(kling_adapter if attempt.provider != "libtv" else None),
                generation_status=(
                    GenerationStatus.PARTIAL
                    if result.status == "PARTIAL"
                    else GenerationStatus.SUCCEEDED
                ),
            )
            await self._release_capacity_if_charge_classified(session, command, attempt)
            await session.commit()
        elif result.status == "FAILED":
            await self._handle_terminal_failure(session, command, attempt, result)
            await self._release_capacity_if_charge_classified(session, command, attempt)
            await session.commit()
        return result.status

    async def _guard_command(
        self, session: AsyncSession, command: GenerationCommand, *, launching: bool
    ) -> bool:
        if command.status not in {CommandStatus.READY, CommandStatus.CLAIMED}:
            return False
        if command.status == CommandStatus.CLAIMED and command.claim_owner != self.owner:
            return False
        values = {"status": GenerationCommand.status}
        if launching:
            values = {
                "status": CommandStatus.LAUNCH_STARTED,
                "launch_started_at": datetime.now(UTC),
            }
        result = await session.execute(
            update(GenerationCommand)
            .where(
                GenerationCommand.id == command.id,
                GenerationCommand.status == command.status,
                GenerationCommand.claim_owner == command.claim_owner,
                GenerationCommand.lease_expires_at == command.lease_expires_at,
                GenerationCommand.launch_started_at.is_(None),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        await session.refresh(command)
        return True

    async def _execute(self, session: AsyncSession, command_id: uuid.UUID) -> bool | None:
        command = await session.get(GenerationCommand, command_id)
        if command is None:
            return True
        attempt = await session.get(GenerationAttempt, command.attempt_id)
        if attempt is None:
            return True
        if attempt.provider != "libtv":
            return await self._execute_kling(session, command, attempt)
        if self.settings.libtv_adapter_mode == "real":
            try:
                require_paid_generation_authorized(self.settings)
            except PaidGenerationBlockedError as exc:
                await self._handle_error(
                    session,
                    command,
                    attempt,
                    LibTVError(exc.code, str(exc)),
                )
                return True
        if not await self._guard_command(session, command, launching=True):
            return None
        attempt.launch_started_at = command.launch_started_at
        await session.commit()
        if self.settings.libtv_adapter_mode == "real":
            try:
                manifest = self.command_host_manager.launch_generation(
                    str(command.id), attempt.project_uuid or "", attempt.video_node_key or ""
                )
            except Exception as exc:
                await self._handle_error(
                    session,
                    command,
                    attempt,
                    LibTVError(
                        "COMMAND_HOST_LAUNCH_UNCERTAIN",
                        str(exc),
                        submitted=True,
                        uncertain=True,
                    ),
                )
                return False
            command.status = CommandStatus.PROCESS_STARTED
            command.process_started_at = datetime.now(UTC)
            attempt.process_started_at = command.process_started_at
            attempt.status = AttemptStatus.RUNNING
            attempt.state_version += 1
            attempt.runner_execution_backend = "WINDOWS_COMMAND_HOST"
            attempt.runner_execution_ref = str(
                self.command_host_manager.manifest_path(str(command.id))
            )
            attempt.runner_process_id = manifest.pid
            attempt.runner_process_started_at = datetime.fromtimestamp(
                manifest.process_started_at, tz=UTC
            )
            attempt.runner_process_nonce = manifest.nonce
            attempt.runner_executable_hash = manifest.executable_hash
            attempt.runner_command_hash = manifest.command_hash
            attempt.stdout_ref = manifest.stdout_file
            attempt.stderr_ref = manifest.stderr_file
            attempt.terminal_result_ref = manifest.result_file
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if variant is not None:
                variant.generation_status = GenerationStatus.RUNNING
            await session.commit()
            await self._aggregate_task(session, attempt.variant_id)
            return False
        command.status = CommandStatus.PROCESS_STARTED
        command.process_started_at = datetime.now(UTC)
        attempt.process_started_at = command.process_started_at
        attempt.status = AttemptStatus.RUNNING
        attempt.state_version += 1
        attempt.runner_execution_backend = (
            "FAKE_IN_PROCESS" if self.settings.libtv_adapter_mode == "fake" else "REPLAY_IN_PROCESS"
        )
        attempt.runner_execution_ref = str(command.id)
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant is not None:
            variant.generation_status = GenerationStatus.RUNNING
        await session.commit()
        await self._aggregate_task(session, attempt.variant_id)
        working_directory = self.settings.runner_root / str(command.id)
        working_directory.mkdir(parents=True, exist_ok=True)
        try:
            result = await self.adapter.run_generation(
                project_uuid=attempt.project_uuid or "",
                node=attempt.video_node_key or "",
                working_directory=working_directory,
            )
        except LibTVError as exc:
            await self._handle_error(session, command, attempt, exc)
            return True
        if result.status != "SUCCEEDED":
            await self._handle_terminal_failure(session, command, attempt, result)
            return True
        await self._handle_success(session, command, attempt, result)
        return True

    async def _execute_kling(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
    ) -> bool:
        if not real_provider_submission_authorized(self.settings):
            await self._handle_error(
                session,
                command,
                attempt,
                KlingCliError(
                    "PAID_GENERATION_BLOCKED",
                    "real provider submissions require live mode, the explicit enable "
                    "switch, and the exact confirmation phrase",
                ),
            )
            return True
        if not self.settings.kling_provider_submission_enabled(attempt.provider):
            await self._handle_error(
                session,
                command,
                attempt,
                KlingCliError(
                    "KLING_PROVIDER_SUBMISSIONS_DISABLED",
                    "The selected video provider is disabled for new submissions",
                ),
            )
            return True
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant is None:
            await self._handle_error(
                session,
                command,
                attempt,
                KlingCliError("KLING_INVALID_INPUT", "generation variant is unavailable"),
            )
            return True
        task = await session.get(ParentTask, variant.parent_task_id)
        if task is None:
            await self._handle_error(
                session,
                command,
                attempt,
                KlingCliError("KLING_INVALID_INPUT", "parent task is unavailable"),
            )
            return True
        assets = list(
            await session.scalars(
                select(Asset).where(Asset.parent_task_id == task.id).order_by(Asset.order_no)
            )
        )
        storyboard_assets = [
            asset
            for asset in assets
            if asset.used_for_generation
            and asset.role not in {"PRODUCT", "REFERENCE_VIDEO", "BACKGROUND_REFERENCE"}
        ]
        product_reference_assets = [
            asset
            for asset in assets
            if asset.used_for_generation and asset.role in {"PRODUCT", "BACKGROUND_REFERENCE"}
        ]
        expected_bindings = variant.generation_config_json.get("referenceImageBindings")
        if expected_bindings is not None:
            actual_bindings = [
                {
                    "imageId": f"image_{index}",
                    "role": "STORYBOARD" if index <= len(storyboard_assets) else "PRODUCT",
                    "assetId": str(asset.id),
                    "sha256": asset.sha256,
                }
                for index, asset in enumerate(
                    [*storyboard_assets, *product_reference_assets], start=1
                )
            ]
            if actual_bindings != expected_bindings:
                await self._handle_error(
                    session,
                    command,
                    attempt,
                    KlingCliError(
                        "KLING_REFERENCE_ROLE_MISMATCH",
                        "Storyboard/product reference bindings changed after preparation",
                    ),
                )
                return True
        storyboard_paths: list[Path] = []
        product_reference_paths: list[Path] = []
        kling_adapter = self._kling_adapter_for(attempt.provider)
        submission_record = None
        submission_invoked = False
        guarded_launch_at = None
        try:
            account_reader = getattr(kling_adapter, "submission_account_id", None)
            if callable(account_reader):
                account_id = await account_reader()
                if attempt.provider_account_id and account_id != attempt.provider_account_id:
                    raise KlingCliError("KLING_ACCOUNT_CHANGED", "Submission account changed")
                attempt.provider_account_id = account_id
            reference_video_path = None
            video_assets = [asset for asset in assets if asset.role == "REFERENCE_VIDEO"]
            if video_assets:
                video = video_assets[0]
                if len(video_assets) != 1 or not video.object_key:
                    raise KlingCliError("WAVESPEED_INPUT_INVALID", "需要一条有效参考视频")
                suffix = ".mov" if video.mime_type == "video/quicktime" else ".mp4"
                reference_video_path = await self.object_store.localize(
                    video.object_key,
                    self.settings.runner_root
                    / "kling"
                    / "inputs"
                    / str(task.id)
                    / f"reference{suffix}",
                )
            for index, asset in enumerate(storyboard_assets, start=1):
                if not asset.object_key:
                    raise KlingCliError(
                        "KLING_INVALID_INPUT",
                        f"Storyboard asset {asset.id} has no source object",
                    )
                suffix = _kling_asset_suffix(asset.original_filename, asset.object_key)
                storyboard_paths.append(
                    await self.object_store.localize(
                        asset.object_key,
                        self.settings.runner_root
                        / "kling"
                        / "inputs"
                        / str(task.id)
                        / f"storyboard-{index}{suffix}",
                    )
                )
            for index, asset in enumerate(product_reference_assets, start=1):
                if not asset.object_key:
                    raise KlingCliError(
                        "KLING_INVALID_INPUT",
                        f"Product reference asset {asset.id} has no source object",
                    )
                suffix = _kling_asset_suffix(asset.original_filename, asset.object_key)
                product_reference_paths.append(
                    await self.object_store.localize(
                        asset.object_key,
                        self.settings.runner_root
                        / "kling"
                        / "inputs"
                        / str(task.id)
                        / f"product-reference-{index}{suffix}",
                    )
                )
            request = VideoGenerationRequest(
                prompt=variant.prompt_text,
                director_native_profile=variant.generation_config_json.get("directorNativeProfile"),
                # Reuse the frozen provider hint; never reselect memory during
                # retries, callback reconciliation or runner restarts.
                director_memory_kling_hint=(
                    variant.generation_config_json.get("directorMemoryKlingHint")
                ),
                reference_video_path=reference_video_path,
                model=attempt.provider_model or self.settings.active_kling_model,
                image_paths=tuple(storyboard_paths),
                duration=int(
                    variant.generation_config_json.get("duration")
                    or variant.generation_config_json.get("durationSeconds")
                    or self.settings.active_kling_duration
                ),
                aspect_ratio=str(
                    variant.generation_config_json.get("aspectRatio")
                    or self.settings.active_kling_aspect_ratio
                ),
                product_reference_paths=tuple(product_reference_paths),
                fallback_mode=str(
                    variant.generation_config_json.get("storyboardFallbackMode")
                    or self.settings.kling_storyboard_fallback_mode
                ),
                # Every variant has its own trace ID; the parent task is shared.
                task_trace_id=attempt.id.hex,
                resolution=str(
                    variant.generation_config_json.get("resolution")
                    or self.settings.active_video_resolution
                ),
                audio=str(
                    variant.generation_config_json.get("audio")
                    or variant.generation_config_json.get("sound")
                    or self.settings.active_video_audio
                ),
                multi_shot=bool(
                    variant.generation_config_json.get(
                        "multiShot",
                        variant.generation_config_json.get(
                            "smartStoryboard",
                            self.settings.active_video_multi_shot,
                        ),
                    )
                ),
                external_task_id=attempt.intent_hash,
                callback_url=(
                    self.settings.kling_api_callback_url
                    if self.settings.kling_provider_backend != "bach"
                    else None
                ),
                element_references=tuple(
                    VideoElementReference(
                        element_id=str(element_id),
                        content_id=f"element_{index}",
                    )
                    for index, element_id in enumerate(
                        variant.generation_config_json.get("elementIds") or (),
                        start=1,
                    )
                ),
            )
            request = await pipeline_request(
                session,
                command,
                attempt,
                task,
                variant,
                request,
                kling_adapter,
                self.object_store,
                self.settings,
            )
            if not await self._guard_command(session, command, launching=True):
                return None
            guarded_launch_at = command.launch_started_at
            attempt.launch_started_at = command.launch_started_at
            await session.commit()
            stage = pipeline_state(command).get("stage", "generation")
            stage_key = f"{attempt.id.hex}:{stage}"
            prior_submission = await session.scalar(
                select(GenerationSubmissionAttempt)
                .where(
                    GenerationSubmissionAttempt.attempt_id == attempt.id,
                    or_(
                        GenerationSubmissionAttempt.idempotency_key == stage_key,
                        GenerationSubmissionAttempt.idempotency_key.like(
                            f"{stage_key}:retry-%"
                        ),
                    ),
                )
                .order_by(GenerationSubmissionAttempt.sequence.desc())
                .limit(1)
            )
            # A confirmed HTTP 429 is a *rejected* request, never a cloud job.
            # Only this exact durable evidence permits another POST for the same
            # attempt. Uncertain/accepted receipts still prohibit blind resubmission.
            safe_capacity_retry = bool(
                prior_submission is not None
                and attempt.provider == self.settings.kling_api_provider_name
                and prior_submission.status == "FAILED_BEFORE_SUBMIT"
                and prior_submission.error_code == "KLING_API_RATE_LIMITED"
                and prior_submission.provider_task_id is None
                and prior_submission.provider == attempt.provider
                and prior_submission.input_fingerprint == attempt.intent_hash
            )
            if prior_submission is not None and not safe_capacity_retry:
                if await self._restore_submission_receipt_handle(session, command, attempt):
                    attempt.status = AttemptStatus.RUNNING
                    attempt.provider_status = "queued"
                    attempt.submission_finished_at = attempt.submission_finished_at or datetime.now(
                        UTC
                    )
                    command.status = CommandStatus.PROCESS_STARTED
                    command.process_started_at = command.process_started_at or datetime.now(UTC)
                    command.lease_expires_at = None
                    await session.commit()
                    return False
                await self._mark_reconciling(
                    session,
                    command,
                    attempt,
                    code="KLING_SUBMISSION_RECOVERY_REQUIRED",
                    message="A prior submit invocation is durable; local retry is forbidden",
                )
                return True
            sequence = max(
                int(attempt.submission_attempt_count or 0),
                int(prior_submission.sequence) if prior_submission is not None else 0,
            ) + 1
            operation_key = f"{attempt.id.hex}:{stage}"
            if prior_submission is not None:
                operation_key = f"{operation_key}:retry-{sequence}"
            if attempt.provider == self.settings.kling_cli_provider_name:
                request = replace(
                    request, submission_operation_key=operation_key,
                    submission_account_id=attempt.provider_account_id,
                    submission_fingerprint=attempt.intent_hash,
                )
            submission_record = GenerationSubmissionAttempt(
                attempt_id=attempt.id,
                sequence=sequence,
                provider=attempt.provider,
                idempotency_key=operation_key,
                input_fingerprint=attempt.intent_hash,
                status="STARTED",
            )
            session.add(submission_record)
            attempt.submission_attempt_count = sequence
            attempt.submission_started_at = datetime.now(UTC)
            attempt.status = AttemptStatus.LAUNCHING
            await session.commit()
            submission_invoked = True
            submission = await kling_adapter.submit(request)
        except KlingCliError as exc:
            if command.status != CommandStatus.LAUNCH_STARTED:
                if not await self._guard_command(session, command, launching=False):
                    return None
            if exc.submitted or exc.uncertain:
                if submission_invoked and submission_record is not None:
                    submission_record.status = "UNCERTAIN"
                    submission_record.finished_at = datetime.now(UTC)
                    submission_record.provider_task_id = (
                        exc.provider_task_id or submission_record.provider_task_id
                    )
                    submission_record.error_code = exc.code
                    submission_record.error_message = str(exc)
                    submission_record.details_json = getattr(exc, "details", None)
                if exc.provider_task_id:
                    attempt.task_id = exc.provider_task_id
                await self._mark_reconciling(
                    session, command, attempt, code=exc.code, message=str(exc), details=exc.details
                )
            else:
                if submission_invoked and submission_record is not None:
                    submission_record.status = "FAILED_BEFORE_SUBMIT"
                    submission_record.finished_at = datetime.now(UTC)
                    submission_record.error_code = exc.code
                    submission_record.error_message = str(exc)
                    submission_record.details_json = getattr(exc, "details", None)
                if (
                    submission_invoked
                    and submission_record is not None
                    and attempt.provider == self.settings.kling_api_provider_name
                    and exc.code == "KLING_API_RATE_LIMITED"
                    and not pipeline_state(command).get("depthTaskId")
                    and not exc.submitted
                    and not exc.uncertain
                ):
                    await self._defer_kling_api_capacity(session, command, attempt, exc)
                else:
                    await self._handle_error(session, command, attempt, exc)
            return True
        except Exception:
            # A failed flush invalidates the transaction; recover the session
            # before reading ORM attributes or writing any recovery verdict.
            await session.rollback()
            await session.refresh(command)
            await session.refresh(attempt)
            # Only an actual adapter submission call may have reached Kling.
            # Pre-invocation failures are local, even after LAUNCH_STARTED was saved;
            # errors after invocation must remain conservative and uncertain.
            if not submission_invoked:
                if command.status == CommandStatus.LAUNCH_STARTED:
                    if (
                        guarded_launch_at is None
                        or command.launch_started_at is None
                        or _utc(command.launch_started_at) != _utc(guarded_launch_at)
                    ):
                        return None
                elif not await self._guard_command(session, command, launching=False):
                    return None
                # A previous invocation may already have a durable receipt even
                # when this invocation failed during local preparation.
                stage = pipeline_state(command).get("stage", "generation")
                saved_receipt = await session.scalar(
                    select(GenerationSubmissionAttempt).where(
                        GenerationSubmissionAttempt.attempt_id == attempt.id,
                        GenerationSubmissionAttempt.idempotency_key
                        == f"{attempt.id.hex}:{stage}",
                    )
                )
                if saved_receipt is not None:
                    await self._mark_reconciling(
                        session, command, attempt,
                        code="KLING_SUBMISSION_RECOVERY_REQUIRED",
                        message=(
                            "Local preparation failed with a prior submit receipt; do not resubmit"
                        ),
                    )
                    return True
                # A recorded launch is not proof the paid provider was invoked.
                await self._handle_error(
                    session,
                    command,
                    attempt,
                    KlingCliError(
                        (
                            "KLING_SUBMISSION_RECORD_FAILED"
                            if submission_record is not None
                            else "KLING_PRE_SUBMIT_FAILED"
                        ),
                        "Local preparation or submit record failed before provider invocation",
                    ),
                )
                return True
            error = KlingCliError(
                "KLING_SUBMISSION_UNCERTAIN",
                "Provider submission failed after durable launch; acceptance is unknown",
                submitted=True,
                uncertain=True,
            )
            if submission_invoked and submission_record is not None:
                # The old ORM receipt is expired by rollback; reload it from disk.
                saved_receipt = await session.scalar(
                    select(GenerationSubmissionAttempt).where(
                        GenerationSubmissionAttempt.idempotency_key == operation_key
                    )
                )
                if saved_receipt is not None:
                    saved_receipt.status = "UNCERTAIN"
                    saved_receipt.finished_at = datetime.now(UTC)
                    saved_receipt.error_code = error.code
                    saved_receipt.error_message = str(error)
            await self._mark_reconciling(
                session, command, attempt, code=error.code, message=str(error)
            )
            return True

        now = datetime.now(UTC)
        # Persist the remote handle before any later local bookkeeping can fail.
        attempt.task_id = submission.provider_task_id
        attempt.provider_model = submission.provider_model
        attempt.provider_status = "queued"
        attempt.provider_raw_status = submission.raw_status
        attempt.submitted_at = attempt.submitted_at or now
        attempt.last_polled_at = None
        attempt.credits_consumed = submission.credits_consumed
        attempt.raw_response_json = submission.raw_response
        clear_provider_error(attempt)
        attempt.status = AttemptStatus.RUNNING
        attempt.submission_finished_at = now
        attempt.state_version += 1
        if submission_record is not None:
            submission_record.status = "ACCEPTED"
            submission_record.finished_at = now
            submission_record.provider_task_id = submission.provider_task_id
        try:
            await session.commit()
        except Exception:
            # The CLI journal already holds the exact remote handle. Roll back
            # before touching expired ORM state, then continue other queued work.
            await session.rollback()
            await session.refresh(command)
            await session.refresh(attempt)
            attempt.task_id = submission.provider_task_id
            attempt.provider_model = submission.provider_model
            attempt.credits_consumed = submission.credits_consumed
            attempt.raw_response_json = submission.raw_response
            saved_receipt = await session.scalar(
                select(GenerationSubmissionAttempt).where(
                    GenerationSubmissionAttempt.idempotency_key == operation_key
                )
            )
            if saved_receipt is not None:
                saved_receipt.provider_task_id = submission.provider_task_id
                saved_receipt.status = "ACCEPTED"
            await self._mark_reconciling(
                session, command, attempt, code="KLING_ACCEPTED_RECEIPT_DB_WRITE_FAILED",
                message="Cloud accepted; recover the exact local receipt without resubmission",
            )
            return True

        record_pipeline_submission(command, task, submission)
        attempt.charge_status = (
            ChargeStatus.CHARGED
            if submission.credits_consumed is not None
            else ChargeStatus.UNKNOWN
        )
        if submission.credits_consumed is not None:
            attempt.billing_source = BillingSource.TASK_BILLING
        attempt.runner_execution_backend = _video_execution_backend(
            attempt.provider,
            self.settings,
        )
        attempt.runner_execution_ref = submission.provider_task_id
        command.status = CommandStatus.PROCESS_STARTED
        command.process_started_at = now
        command.lease_expires_at = None
        variant.generation_status = GenerationStatus.RUNNING
        task.phase = ParentTaskPhase.GENERATING
        task.state_version += 1
        await session.commit()
        return False

    async def _mark_reconciling(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
        *,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = AttemptStatus.RECONCILING
        attempt.charge_status = ChargeStatus.UNKNOWN
        attempt.provider_status = "reconciling"
        attempt.last_reconciliation_error = message[:500]
        attempt.last_reconciliation_at = now
        attempt.submission_finished_at = attempt.submission_finished_at or now
        attempt.next_reconciliation_at = now + timedelta(seconds=_KLING_RECONCILIATION_BASE_SECONDS)
        record_provider_error(
            attempt,
            code=code,
            message=message,
            details={**(details or {}), "reconciliation": True},
            retryable=True,
        )
        command.status = CommandStatus.UNCERTAIN
        command.lease_expires_at = None
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt.id)
        )
        if reservation:
            await settle_points(
                session, reservation, actual_points=None, charge_status=ChargeStatus.UNKNOWN
            )
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant:
            variant.generation_status = GenerationStatus.UNKNOWN
        attempt.state_version += 1
        await session.commit()
        await self._aggregate_task(session, attempt.variant_id)

    async def _claim_poll_observation(
        self, session: AsyncSession, attempt: GenerationAttempt
    ) -> bool | None:
        # Acquire a write guard after network I/O; a pre-query snapshot may be stale.
        claimed = await session.execute(
            update(GenerationAttempt)
            .where(
                GenerationAttempt.id == attempt.id,
                or_(
                    GenerationAttempt.status.in_(
                        (AttemptStatus.RUNNING, AttemptStatus.RECONCILING)
                    ),
                    and_(
                        GenerationAttempt.provider == self.settings.kling_api_provider_name,
                        GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT,
                        GenerationAttempt.task_id.is_not(None),
                        GenerationAttempt.task_id != "",
                    ),
                ),
                GenerationAttempt.state_version == attempt.state_version,
            )
            .values(state_version=GenerationAttempt.state_version + 1)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.commit()
            return False
        await session.refresh(attempt)
        return True

    async def poll_kling_tasks(self, session: AsyncSession) -> int:
        if not any(
            self.settings.kling_provider_enabled(provider)
            for provider in self.settings.kling_provider_names
        ):
            return 0
        now = datetime.now(UTC)
        attempts = list(
            await session.scalars(
                select(GenerationAttempt)
                .where(
                    GenerationAttempt.provider.in_(self.settings.kling_provider_names),
                    or_(
                        GenerationAttempt.status.in_(
                            (AttemptStatus.RUNNING, AttemptStatus.RECONCILING)
                        ),
                        and_(
                            GenerationAttempt.provider == self.settings.kling_api_provider_name,
                            GenerationAttempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT,
                            GenerationAttempt.task_id.is_not(None),
                            GenerationAttempt.task_id != "",
                        ),
                    ),
                    or_(
                        GenerationAttempt.next_reconciliation_at.is_(None),
                        GenerationAttempt.next_reconciliation_at <= now,
                    ),
                )
                .order_by(GenerationAttempt.last_polled_at, GenerationAttempt.created_at)
                .limit(
                    max(
                        self.settings.kling_cli_max_query_concurrency,
                        self.settings.kling_api_max_poll_concurrency,
                    )
                    * 4
                )
            )
        )
        processed = 0
        # Batch remote observations independently of DB writes. The official
        # API's read-only requests can run concurrently (default four), while
        # terminal transitions are still applied serially with state_version CAS.
        # Commit the SELECT transaction before waiting on external HTTP calls.
        if self.settings.classic_throughput_s1s3_enabled:
            await session.commit()
        api_candidates = []
        for observed in attempts:
            if (
                not self.settings.classic_throughput_s1s3_enabled
                or observed.provider != self.settings.kling_api_provider_name
                or not observed.task_id
                or not self.settings.kling_provider_enabled(observed.provider)
            ):
                continue
            submitted_at = observed.submitted_at
            if (
                submitted_at is not None
                and observed.status == AttemptStatus.RUNNING
                and (now - _utc(submitted_at)).total_seconds()
                >= self.settings.kling_api_task_timeout_seconds
            ):
                continue  # preserve the explicit timeout path in the state loop
            if observed.last_polled_at is not None:
                interval = self.settings.kling_poll_interval_for_provider(
                    observed.provider,
                    unknown=observed.provider_status == "unknown",
                )
                if (now - _utc(observed.last_polled_at)).total_seconds() < interval:
                    continue
            api_candidates.append(observed)

        query_limit = asyncio.Semaphore(min(4, self.settings.kling_api_max_poll_concurrency))

        async def observe_api(candidate: GenerationAttempt):
            async with query_limit:
                api_adapter = self._kling_adapter_for(candidate.provider)
                try:
                    account_reader = getattr(api_adapter, "submission_account_id", None)
                    if candidate.provider_account_id and callable(account_reader):
                        if await account_reader() != candidate.provider_account_id:
                            raise KlingCliError(
                                "KLING_ACCOUNT_CHANGED",
                                "Original submission account is not active",
                            )
                    return await retry_kling_call(
                        lambda: api_adapter.query(candidate.task_id or ""),
                        attempts=self.settings.kling_api_max_retries,
                    )
                except Exception as exc:
                    return exc

        api_observations = dict(zip(
            (candidate.id for candidate in api_candidates),
            await asyncio.gather(*(observe_api(candidate) for candidate in api_candidates)),
            strict=True,
        ))

        for attempt in attempts:
            due = attempt.next_reconciliation_at
            if due and _utc(due) > now:
                continue
            submitted_at = attempt.submitted_at
            if submitted_at is not None:
                if submitted_at.tzinfo is None:
                    submitted_at = submitted_at.replace(tzinfo=UTC)
                task_timeout = self.settings.video_task_timeout_for_provider(attempt.provider)
                if (
                    task_timeout is not None
                    and attempt.status == AttemptStatus.RUNNING
                    and (now - submitted_at).total_seconds() >= task_timeout
                ):
                    if not await self._claim_poll_observation(session, attempt):
                        continue
                    command = await session.scalar(
                        select(GenerationCommand).where(GenerationCommand.attempt_id == attempt.id)
                    )
                    error_code, error_message = _video_timeout_error(
                        attempt.provider,
                        self.settings,
                    )
                    if (
                        command is not None
                        and attempt.provider == self.settings.kling_cli_provider_name
                    ):
                        # A timed-out CLI job still has a durable generationId.
                        # Keep querying that ID read-only before escalating this job.
                        await self._advance_reconciliation(
                            session, command, attempt,
                            KlingCliError(error_code, error_message, retryable=True),
                        )
                        processed += 1
                        continue
                    attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
                    attempt.provider_status = "unknown"
                    record_provider_error(
                        attempt,
                        code=error_code,
                        message=error_message,
                        details={"providerTaskId": attempt.task_id},
                        retryable=True,
                    )
                    if command is not None:
                        command.status = CommandStatus.UNCERTAIN
                    variant = await session.get(GenerationVariant, attempt.variant_id)
                    if variant is not None:
                        variant.generation_status = GenerationStatus.UNKNOWN
                    await session.commit()
                    if variant is not None:
                        await self._aggregate_task(session, variant.id)
                    processed += 1
                    continue
            last_polled = attempt.last_polled_at
            if last_polled is not None:
                if last_polled.tzinfo is None:
                    last_polled = last_polled.replace(tzinfo=UTC)
                poll_interval = self.settings.kling_poll_interval_for_provider(
                    attempt.provider,
                    unknown=attempt.provider_status == "unknown",
                )
                if (now - last_polled).total_seconds() < poll_interval:
                    continue
            command = await session.scalar(
                select(GenerationCommand).where(GenerationCommand.attempt_id == attempt.id)
            )
            if command is None or not attempt.task_id:
                if command is not None and attempt.status == AttemptStatus.RECONCILING:
                    if await self._claim_poll_observation(session, attempt):
                        await self._advance_reconciliation_without_handle(session, command, attempt)
                continue
            if not self.settings.kling_provider_enabled(attempt.provider):
                continue
            state = pipeline_state(command)
            stage = state.get("stage")
            if stage and not state.get(f"{stage}TaskId"):
                # A restart can land after the handle commit but before local
                # pipeline bookkeeping. Reconstruct only from that durable receipt.
                variant = await session.get(GenerationVariant, attempt.variant_id)
                task = await session.get(ParentTask, variant.parent_task_id) if variant else None
                if task:
                    record_pipeline_submission(
                        command,
                        task,
                        ProviderSubmissionResult(
                            attempt.task_id,
                            attempt.provider_raw_status or "submitted",
                            attempt.credits_consumed,
                            attempt.provider_model or "",
                            attempt.raw_response_json or {},
                        ),
                    )
                    await session.commit()
            kling_adapter = self._kling_adapter_for(attempt.provider)
            try:
                if attempt.id in api_observations:
                    result = api_observations[attempt.id]
                    if isinstance(result, Exception):
                        raise result
                else:
                    account_reader = getattr(kling_adapter, "submission_account_id", None)
                    if attempt.provider_account_id and callable(account_reader):
                        if await account_reader() != attempt.provider_account_id:
                            raise KlingCliError(
                                "KLING_ACCOUNT_CHANGED", "Original submission account is not active"
                            )
                    result = await retry_kling_call(
                        lambda attempt=attempt, adapter=kling_adapter: adapter.query(
                            attempt.task_id or ""
                        ),
                        attempts=(
                            self.settings.kling_api_max_retries
                            if attempt.provider == self.settings.kling_api_provider_name
                            else 3
                        ),
                    )
            except KlingCliError as exc:
                if not await self._claim_poll_observation(session, attempt):
                    continue
                await self._advance_reconciliation(session, command, attempt, exc)
                processed += 1
                continue

            except Exception:
                if not await self._claim_poll_observation(session, attempt):
                    continue
                await self._advance_reconciliation(
                    session,
                    command,
                    attempt,
                    KlingCliError(
                        "VIDEO_PROVIDER_POLL_FAILED", "Provider query failed", retryable=True
                    ),
                )
                processed += 1
                continue

            if not await self._claim_poll_observation(session, attempt):
                continue
            attempt.last_polled_at = datetime.now(UTC)
            attempt.last_reconciliation_at = attempt.last_polled_at
            attempt.next_reconciliation_at = None
            attempt.last_reconciliation_error = None
            attempt.provider_status = result.status
            attempt.provider_raw_status = result.raw_status
            attempt.raw_response_json = result.raw_response
            overdue_cloud_job = (
                attempt.status == AttemptStatus.UNKNOWN_AFTER_SUBMIT
                and attempt.provider == self.settings.kling_api_provider_name
                and result.status in {"queued", "running"}
                and attempt.submitted_at is not None
                and (datetime.now(UTC) - _utc(attempt.submitted_at)).total_seconds()
                >= self.settings.kling_api_task_timeout_seconds
            )
            if result.status in {"queued", "running", "succeeded"}:
                if overdue_cloud_job:
                    # The provider says the overdue job is still active: retain its
                    # cloud slot and keep querying, rather than re-triggering timeout.
                    command.status = CommandStatus.UNCERTAIN
                    attempt.next_reconciliation_at = datetime.now(UTC) + timedelta(
                        seconds=self.settings.kling_api_max_poll_interval_seconds
                    )
                else:
                    attempt.status = AttemptStatus.RUNNING
                    command.status = CommandStatus.PROCESS_STARTED
                    command.lease_expires_at = None
                    clear_provider_error(attempt)
            if result.credits_consumed is not None:
                attempt.credits_consumed = result.credits_consumed
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if (
                result.status in {"succeeded", "partial"}
                or map_kling_status(result.raw_status) in {"succeeded", "partial"}
            ) and (not result.outputs or not result.outputs[0].url):
                await self._advance_reconciliation(
                    session,
                    command,
                    attempt,
                    KlingCliError("VIDEO_OUTPUT_NOT_READY", "Provider output URL is not ready"),
                )
                processed += 1
                continue
            if (
                variant is not None
                and result.status == "succeeded"
                and pipeline_state(command).get("stage") == "depth"
            ):
                task = await session.get(ParentTask, variant.parent_task_id)
                try:
                    await queue_replacement(
                        session,
                        command,
                        attempt,
                        variant,
                        task,
                        result,
                    )
                except KlingCliError as exc:
                    await self._handle_error(session, command, attempt, exc)
                processed += 1
                continue
            if result.status in {"queued", "running"}:
                attempt.reconciliation_attempt_count = 0
                if variant:
                    variant.generation_status = GenerationStatus.RUNNING
                await session.commit()
                if variant:
                    await self._aggregate_task(session, variant.id)
            elif result.status == "unknown":
                logger.warning(
                    "Kling task returned an unknown status",
                    extra={
                        "attempt_id": str(attempt.id),
                        "raw_status": result.raw_status,
                    },
                )
                await self._advance_reconciliation(
                    session,
                    command,
                    attempt,
                    KlingCliError(
                        "KLING_REMOTE_STATUS_UNKNOWN", "Provider returned unknown status"
                    ),
                )
                if variant:
                    variant.generation_status = GenerationStatus.UNKNOWN
            elif result.status == "failed":
                attempt.provider_status = "failed"
                attempt.completed_at = datetime.now(UTC)
                record_provider_error(
                    attempt,
                    code=(
                        KLING_RISK_CONTROL_ERROR
                        if attempt.provider == self.settings.kling_api_provider_name
                        and _kling_risk_control_rejected(result.message)
                        else "KLING_REMOTE_TASK_FAILED"
                    ),
                    message=result.message or "Kling remote generation failed",
                    details={
                        "providerTaskId": attempt.task_id,
                        "providerStatus": result.raw_status,
                    },
                    retryable=False,
                )
                await self._handle_terminal_failure(
                    session,
                    command,
                    attempt,
                    GenerationResult(
                        status="FAILED",
                        payload={
                            **result.raw_response,
                            "taskId": attempt.task_id,
                        },
                    ),
                )
            else:
                output = result.outputs[0]
                attempt.output_url = output.url
                attempt.completed_at = datetime.now(UTC)
                await self._handle_success(
                    session,
                    command,
                    attempt,
                    GenerationResult(
                        status="SUCCEEDED",
                        payload={
                            **result.raw_response,
                            "taskId": attempt.task_id,
                            "videoUrl": output.url,
                            "actualPoints": attempt.credits_consumed,
                            "billingSource": "TASK_BILLING",
                        },
                    ),
                    download_adapter=kling_adapter,
                    defer_delivery=self.settings.classic_throughput_s1s3_enabled,
                    generation_status=(
                        GenerationStatus.PARTIAL
                        if result.status == "partial"
                        else GenerationStatus.SUCCEEDED
                    ),
                )
            processed += 1
        return processed

    async def _recover_history_handle(self, session, attempt, adapter) -> bool:
        finder = getattr(adapter, "find_recent_submission", None)
        start = attempt.submission_started_at or attempt.launch_started_at
        # The API can accept a request up to its own HTTP timeout; do not
        # apply the shorter CLI command window to exact external-ID recovery.
        recovery_window = (
            timedelta(seconds=self.settings.kling_api_request_timeout_seconds + 30)
            if attempt.provider in {"kling", self.settings.kling_api_provider_name}
            else timedelta(milliseconds=self.settings.kling_cli_command_timeout_ms)
        )
        end = attempt.submission_finished_at or (
            _utc(start) + recovery_window if start else None
        )
        if not (
            callable(finder)
            and attempt.provider_account_id
            and start
            and end
            and attempt.intent_hash
        ):
            raise KlingCliError(
                "KLING_HISTORY_EVIDENCE_MISSING",
                "Missing account, time window or input fingerprint; no history guess",
            )
        match = await finder(
            account_id=attempt.provider_account_id,
            submitted_after=_utc(start),
            submitted_before=_utc(end),
            input_fingerprint=attempt.intent_hash,
        )
        if not (
            isinstance(match, ProviderSubmissionMatch)
            and match.provider_task_id
            and match.account_id == attempt.provider_account_id
            and match.input_fingerprint == attempt.intent_hash
            and match.submitted_at
            and _utc(start) <= _utc(match.submitted_at) <= _utc(end)
        ):
            raise KlingCliError("KLING_HISTORY_NO_EXACT_MATCH", "No unique evidenced history match")
        attempt.task_id = match.provider_task_id
        attempt.submitted_at = match.submitted_at
        attempt.credits_consumed = match.credits_consumed
        attempt.raw_response_json = match.raw_response
        await session.commit()
        return True

    async def _restore_submission_receipt_handle(self, session, command, attempt) -> bool:
        """Read the exact local receipt; never borrow a prior pipeline stage's ID."""
        stage = pipeline_state(command).get("stage", "generation")
        operation_key = f"{attempt.id.hex}:{stage}"
        record = await session.scalar(
            select(GenerationSubmissionAttempt)
            .where(
                GenerationSubmissionAttempt.attempt_id == attempt.id,
                or_(
                    GenerationSubmissionAttempt.idempotency_key == operation_key,
                    GenerationSubmissionAttempt.idempotency_key.like(
                        f"{operation_key}:retry-%"
                    ),
                ),
            )
            .order_by(GenerationSubmissionAttempt.sequence.desc())
            .limit(1)
        )
        if record is None:
            return False
        if (not record.provider_task_id
                and attempt.provider == self.settings.kling_cli_provider_name):
            if (record.provider != attempt.provider
                    or record.input_fingerprint != attempt.intent_hash
                    or record.status not in {"STARTED", "ACCEPTED", "UNCERTAIN"}):
                raise KlingCliError(
                    "KLING_RECEIPT_EVIDENCE_MISMATCH",
                    "Saved submit boundary does not match the CLI receipt",
                    submitted=True, uncertain=True,
                )
            if not attempt.provider_account_id or not attempt.intent_hash:
                return False
            try:
                receipt = KlingSubmissionReceipt(
                    self.settings.runner_root, record.idempotency_key, attempt.provider,
                    attempt.provider_account_id, attempt.intent_hash,
                ).load()
            except (OSError, ValueError) as exc:
                raise KlingCliError(
                    "KLING_RECEIPT_EVIDENCE_MISMATCH",
                    "Local CLI receipt is unreadable or does not match this operation",
                    submitted=True, uncertain=True,
                ) from exc
            if receipt and receipt["status"] in {"FAILED_BEFORE_SUBMIT", "REJECTED"}:
                record.status = "FAILED_BEFORE_SUBMIT"
                record.error_code = receipt.get("errorCode") or "KLING_PRE_SUBMIT_FAILED"
                record.finished_at = datetime.now(UTC)
                raise KlingCliError(
                    record.error_code,
                    "Exact local CLI receipt proves this invocation did not create a paid task",
                    details={"verifiedBeforeSubmit": True, "generationInvoked": False},
                )
            if receipt and receipt.get("providerTaskId"):
                record.provider_task_id = receipt["providerTaskId"]
                record.status = receipt["status"]
                credits = receipt.get("creditsConsumed")
                if type(credits) is int and credits >= 0:
                    attempt.credits_consumed = credits
                attempt.raw_response_json = {"generationId": receipt["providerTaskId"]}
        if not record.provider_task_id:
            return False
        if not (
            record.provider == attempt.provider
            and record.status in {"ACCEPTED", "UNCERTAIN"}
            and record.input_fingerprint
            and record.input_fingerprint == attempt.intent_hash
        ):
            raise KlingCliError(
                "KLING_RECEIPT_EVIDENCE_MISMATCH",
                "Saved receipt does not match this provider, operation and input",
                submitted=True,
                uncertain=True,
            )
        attempt.task_id = record.provider_task_id
        attempt.submitted_at = attempt.submitted_at or record.started_at
        attempt.submission_finished_at = (
            attempt.submission_finished_at or record.finished_at
        )
        return True

    async def _advance_reconciliation_without_handle(self, session, command, attempt) -> None:
        adapter = self._kling_adapter_for(attempt.provider)
        try:
            if not await self._restore_submission_receipt_handle(session, command, attempt):
                await self._recover_history_handle(session, attempt, adapter)
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, KlingCliError)
                else KlingCliError(
                    "KLING_HISTORY_QUERY_FAILED", "Historical lookup failed", retryable=True
                )
            )
            if error.details.get("verifiedBeforeSubmit") and not (
                error.submitted or error.uncertain
            ):
                await self._handle_error(session, command, attempt, error)
                return
            if error.code == "KLING_HISTORY_QUERY_UNSUPPORTED":
                # Repeating an unsupported lookup cannot recover a lost handle.
                # Preserve the original submit error instead of replacing it with
                # a misleading generic 'reconciliation exhausted' verdict.
                attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
                attempt.provider_status = "unknown"
                attempt.last_reconciliation_error = str(error)[:500]
                attempt.next_reconciliation_at = None
                attempt.provider_error_retryable = False
                command.status = CommandStatus.UNCERTAIN
                variant = await session.get(GenerationVariant, attempt.variant_id)
                if variant:
                    variant.generation_status = GenerationStatus.UNKNOWN
                await session.commit()
                if variant:
                    await self._aggregate_task(session, variant.id)
                return
            await self._advance_reconciliation(session, command, attempt, error)
            return
        attempt.status = AttemptStatus.RUNNING
        command.status = CommandStatus.PROCESS_STARTED
        command.lease_expires_at = None
        attempt.next_reconciliation_at = None
        await session.commit()

    async def _advance_reconciliation(self, session, command, attempt, exc: KlingCliError) -> None:
        now = datetime.now(UTC)
        attempt.status = AttemptStatus.RECONCILING
        attempt.last_polled_at = now
        attempt.last_reconciliation_error = str(exc)[:500]
        attempt.reconciliation_attempt_count = int(attempt.reconciliation_attempt_count or 0) + 1
        attempt.last_reconciliation_at = now
        attempt.provider_status = "reconciling"
        command.status = CommandStatus.UNCERTAIN
        delay = min(
            _KLING_RECONCILIATION_BASE_SECONDS
            * 2 ** min(attempt.reconciliation_attempt_count - 1, 10),
            _KLING_RECONCILIATION_MAX_SECONDS,
        )
        attempt.next_reconciliation_at = now + timedelta(seconds=delay)
        record_provider_error(
            attempt, code=exc.code, message=str(exc), details=exc.details, retryable=True
        )
        if attempt.reconciliation_attempt_count >= _MAX_KLING_RECONCILIATION_ATTEMPTS:
            attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
            attempt.provider_status = "unknown"
            known_api_handle = (
                attempt.provider == self.settings.kling_api_provider_name
                and bool(attempt.task_id)
            )
            # A durable official API handle is still safe to check read-only.
            # Bounded periodic retries prevent a temporary provider outage
            # from turning an UNKNOWN cloud job into a permanent slot leak.
            attempt.next_reconciliation_at = (
                now + timedelta(seconds=_KLING_RECONCILIATION_MAX_SECONDS)
                if known_api_handle else None
            )
            record_provider_error(
                attempt,
                code="KLING_RECONCILIATION_EXHAUSTED",
                message=(
                    "Provider query retries exhausted; automatic read-only "
                    "checks continue. Do not resubmit."
                    if known_api_handle
                    else "Read-only recovery exhausted; manual evidence required. "
                    "Do not resubmit or change accounts to retry."
                ),
                details={"lastErrorCode": exc.code},
                retryable=False,
            )
            if (
                not known_api_handle
                or attempt.reconciliation_attempt_count == _MAX_KLING_RECONCILIATION_ATTEMPTS
                or attempt.reconciliation_attempt_count % 24 == 0
            ):
                logger.error(
                    "Kling reconciliation needs continued read-only verification",
                    extra={
                        "attempt_id": str(attempt.id),
                        "has_task_id": bool(attempt.task_id),
                    },
                )
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant:
            variant.generation_status = GenerationStatus.UNKNOWN
        await session.commit()
        if variant:
            await self._aggregate_task(session, variant.id)

    async def observe_command_hosts(self, session: AsyncSession) -> int:
        if self.settings.libtv_adapter_mode != "real":
            return 0
        commands = list(
            await session.scalars(
                select(GenerationCommand).where(
                    GenerationCommand.status == CommandStatus.PROCESS_STARTED,
                    GenerationCommand.attempt_id.in_(
                        select(GenerationAttempt.id).where(GenerationAttempt.provider == "libtv")
                    ),
                )
            )
        )
        observed = 0
        for command in commands:
            attempt = await session.get(GenerationAttempt, command.attempt_id)
            if attempt is None:
                continue
            try:
                state = self.command_host_manager.observe(str(command.id))
            except (OSError, ValueError):
                state = None
            if state is not None and state.status == "RUNNING":
                await renew_capacity(session, command.id)
                await session.commit()
                await self._aggregate_task(session, attempt.variant_id)
                observed += 1
                continue
            if state is not None and state.status == "TERMINAL" and state.terminal_result:
                terminal = state.terminal_result
                if terminal.get("ok"):
                    result = GenerationResult(**terminal["result"])
                    if result.status == "SUCCEEDED":
                        await self._handle_success(session, command, attempt, result)
                    else:
                        await self._handle_terminal_failure(session, command, attempt, result)
                    await self._release_capacity_if_charge_classified(session, command, attempt)
                    await session.commit()
                else:
                    error_payload = terminal.get("error")
                    if isinstance(error_payload, dict):
                        terminal_error = LibTVError(
                            str(error_payload.get("code") or "COMMAND_HOST_TERMINAL_ERROR"),
                            str(error_payload.get("message") or "command-host failed"),
                            submitted=bool(error_payload.get("submitted")),
                            uncertain=bool(error_payload.get("uncertain")),
                        )
                    else:
                        terminal_error = LibTVError(
                            "COMMAND_HOST_TERMINAL_ERROR",
                            str(error_payload or "command-host failed"),
                            submitted=True,
                            uncertain=True,
                        )
                    await self._handle_error(
                        session,
                        command,
                        attempt,
                        terminal_error,
                    )
            else:
                code = f"COMMAND_HOST_{state.status}" if state is not None else "COMMAND_HOST_LOST"
                await self._handle_error(
                    session,
                    command,
                    attempt,
                    LibTVError(
                        code,
                        "command-host identity or result is uncertain",
                        submitted=True,
                        uncertain=True,
                    ),
                )
            observed += 1
        return observed

    async def recover_stale_launch_started_commands(self, session: AsyncSession) -> int:
        """Resolve commands abandoned between LAUNCH_STARTED and PROCESS_STARTED.

        _execute and _execute_kling both commit LAUNCH_STARTED *before* handing the request
        to the provider, because that is the point past which the provider may already have
        spent the credit. If the runner then dies -- restart, crash, kill -- that commit
        survives while the follow-up PROCESS_STARTED commit never happens, and nothing in
        the system looks at the command again:

        * _run_ready_provider only claims CommandStatus.READY
        * observe_command_hosts only scans PROCESS_STARTED, and only for libtv

        Production held exactly one such row -- command 7451a3e5..., attempt 39988a44...
        (provider kling_official_cli), lease expired 2026-07-30 08:42 with no
        process_started_at.

        Recovery must never re-submit. The spend may already have happened, and there is no
        handle to query: attempt.task_id is written only after the provider call returns,
        which is precisely the step that never completed. So the command is resolved as
        submitted-and-uncertain -- the same verdict _handle_error applies when a provider
        call itself raises.

        That verdict deliberately does *not* free the intent: UNKNOWN_AFTER_SUBMIT is still
        inside ACTIVE_ATTEMPT_SQL, so uq_attempt_active_intent stays occupied and the task
        cannot be relaunched. That is the intended guard -- the credit may be gone, and
        re-running would double-spend. What changes is visibility: the row leaves the state
        nothing scans and becomes an UNCERTAIN command with an UNKNOWN charge, i.e. it counts
        in the reconciliation backlog and is resolvable by an operator. Clearing the lease
        only ends the claim that no live process is serving.
        """
        from libtv_agent.runner.launch_recovery import recover_abandoned_launches

        return await recover_abandoned_launches(self, session)

    async def _defer_kling_api_capacity(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
        error: KlingCliError,
    ) -> None:
        """Persist a confirmed rejection as delayed READY, never as a paid job.

        A provider 429 without a task handle is safe to retry, unlike a timeout
        or a failed receipt write. Retries survive runner restarts and do not
        consume the cloud-active task budget until accepted.
        """
        now = datetime.now(UTC)
        delay = min(600, 30 * (2 ** min(max(int(attempt.submission_attempt_count or 1) - 1, 0), 5)))
        attempt.status = AttemptStatus.DISPATCH_PENDING
        attempt.provider_status = "waiting_capacity"
        attempt.charge_status = ChargeStatus.NOT_CHARGED
        attempt.submission_finished_at = now
        attempt.launch_started_at = None
        attempt.state_version += 1
        record_provider_error(
            attempt, code=error.code, message=str(error),
            details=getattr(error, "details", None), retryable=True,
        )
        command.status = CommandStatus.READY
        command.claim_owner = None
        command.launch_started_at = None
        command.lease_expires_at = now + timedelta(seconds=delay)
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant is not None:
            variant.generation_status = GenerationStatus.PENDING
        await session.commit()
        await self._aggregate_task(session, attempt.variant_id)

    async def _handle_error(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
        error: LibTVError | KlingCliError,
    ) -> None:
        if error.code == "LIBTV_AUTH_REQUIRED":
            await set_libtv_auth_required(
                session,
                self.settings,
                required=True,
                reason="automatic pause: LibTV CLI reported an expired login",
            )
        prior_depth_paid_boundary = bool(pipeline_state(command).get("depthTaskId"))
        # An earlier accepted depth task does not turn a confirmed 4xx rejection
        # of the subsequent replacement POST into an unknown cloud submission.
        # The depth charge itself must still be tracked conservatively.
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt.id)
        )
        if error.submitted or error.uncertain:
            attempt.status = AttemptStatus.UNKNOWN_AFTER_SUBMIT
            attempt.charge_status = ChargeStatus.UNKNOWN
            command.status = CommandStatus.UNCERTAIN
            if reservation:
                await settle_points(
                    session, reservation, actual_points=None, charge_status=ChargeStatus.UNKNOWN
                )
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if variant:
                variant.generation_status = GenerationStatus.UNKNOWN
        else:
            attempt.status = AttemptStatus.FAILED_BEFORE_SUBMIT
            # This replacement was rejected, but the prior depth stage might
            # already have consumed credits. Do not label the whole flow unpaid.
            prior_charge = (
                ChargeStatus.UNKNOWN if prior_depth_paid_boundary else ChargeStatus.NOT_CHARGED
            )
            attempt.charge_status = prior_charge
            attempt.submission_finished_at = attempt.submission_finished_at or datetime.now(UTC)
            command.status = CommandStatus.TERMINAL
            command.lease_expires_at = None
            if reservation:
                await settle_points(
                    session, reservation, actual_points=None, charge_status=prior_charge
                )
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if variant:
                variant.generation_status = GenerationStatus.FAILED
        if attempt.provider != "libtv":
            record_provider_error(
                attempt,
                code=error.code,
                message=str(error),
                details=getattr(error, "details", None),
                retryable=getattr(error, "retryable", None),
            )
        else:
            attempt.last_error_code = error.code
            attempt.error_message = str(error)
        if attempt.provider != "libtv":
            attempt.provider_status = "unknown" if error.submitted or error.uncertain else "failed"
        attempt.state_version += 1
        await session.commit()
        await self._aggregate_task(session, attempt.variant_id)

    async def _handle_terminal_failure(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
        result: GenerationResult,
    ) -> None:
        attempt.status = AttemptStatus.FAILED_TERMINAL
        attempt.task_id = str(result.payload.get("taskId") or "")
        if attempt.last_error_code != KLING_RISK_CONTROL_ERROR:
            attempt.last_error_code = (
                "KLING_REMOTE_TASK_FAILED"
                if attempt.provider != "libtv"
                else "GENERATION_TERMINAL_FAILED"
            )
        attempt.provider_status = "failed"
        attempt.completed_at = attempt.completed_at or datetime.now(UTC)
        if result.payload:
            attempt.raw_response_json = result.payload
        # A failed cloud task may still charge. Mark unpaid only when Kling's
        # own complete task receipt explicitly contains zero billing entries.
        # Earlier paid depth stages must not be erased by a zero replacement bill.
        if (
            attempt.provider == self.settings.kling_api_provider_name
            and attempt.charge_status != ChargeStatus.CHARGED
            and not pipeline_state(command).get("depthTaskId")
            and _provider_receipt_confirms_zero_billing(result.payload)
        ):
            attempt.credits_consumed = 0
            attempt.charge_status = ChargeStatus.NOT_CHARGED
            attempt.billing_source = BillingSource.TASK_BILLING
        if attempt.provider != "libtv" and not attempt.error_message:
            record_provider_error(
                attempt,
                code="KLING_REMOTE_TASK_FAILED",
                message="Kling remote generation failed",
                details={"providerTaskId": attempt.task_id},
                retryable=False,
            )
        command.status = CommandStatus.TERMINAL
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt.id)
        )
        if reservation:
            if attempt.provider != "libtv":
                charge_status = attempt.charge_status
            else:
                charge_status = (
                    ChargeStatus.UNKNOWN
                    if self.settings.libtv_adapter_mode == "real"
                    else ChargeStatus.NOT_CHARGED
                )
            attempt.charge_status = charge_status
            await settle_points(
                session, reservation, actual_points=None, charge_status=charge_status
            )
        variant = await session.get(GenerationVariant, attempt.variant_id)
        if variant:
            variant.generation_status = GenerationStatus.FAILED
        await session.commit()
        await self._aggregate_task(session, attempt.variant_id)

    async def _handle_success(
        self,
        session: AsyncSession,
        command: GenerationCommand,
        attempt: GenerationAttempt,
        result: GenerationResult,
        *,
        download_adapter: object | None = None,
        generation_status: GenerationStatus = GenerationStatus.SUCCEEDED,
        defer_delivery: bool = False,
    ) -> None:
        attempt.status = AttemptStatus.SUCCEEDED
        clear_provider_error(attempt)
        attempt.task_id = str(result.payload.get("taskId") or attempt.task_id or "")
        attempt.provider_status = (
            "partial" if generation_status == GenerationStatus.PARTIAL else "succeeded"
        )
        attempt.completed_at = attempt.completed_at or datetime.now(UTC)
        attempt.output_url = str(result.payload.get("videoUrl") or attempt.output_url or "")
        attempt.raw_response_json = result.payload
        actual_points = result.payload.get("actualPoints")
        billing_source = result.payload.get("billingSource")
        has_attributable_billing = (
            isinstance(actual_points, int)
            and not isinstance(actual_points, bool)
            and actual_points >= 0
            and billing_source in {"TASK_BILLING", "PRICE_SNAPSHOT"}
        )
        if attempt.provider == "libtv" and self.settings.libtv_adapter_mode != "real":
            has_attributable_billing = True
            actual_points = None
            billing_source = "PRICE_SNAPSHOT"
        elif attempt.provider != "libtv":
            has_attributable_billing = attempt.credits_consumed is not None
            actual_points = attempt.credits_consumed
            billing_source = "TASK_BILLING" if has_attributable_billing else None
        attempt.charge_status = (
            ChargeStatus.CHARGED if has_attributable_billing else ChargeStatus.UNKNOWN
        )
        attempt.billing_source = BillingSource(billing_source) if has_attributable_billing else None
        command.status = CommandStatus.TERMINAL
        command.lease_expires_at = None
        try:
            variant = await session.get(GenerationVariant, attempt.variant_id)
            if variant is None:
                return
            variant.generation_status = generation_status
            variant.download_status = (
                DownloadStatus.QUEUED if defer_delivery else DownloadStatus.DOWNLOADING
            )
            await session.commit()
            await self._aggregate_task(session, variant.id)
            if defer_delivery:
                # Record remote success and settle its billing before CDN I/O.
                return
            task = await session.get(ParentTask, variant.parent_task_id)
            if task is None:
                return
            filename = delivery_filename(
                business_date().isoformat(),
                str(task.user_id),
                str(task.id),
                variant.variant_key or variant.variant_type.value,
                variant.revision,
            )
            destination = self.settings.runner_root / "downloads" / str(task.id) / filename
            existing_artifact = await session.scalar(
                select(DownloadArtifact)
                .where(
                    DownloadArtifact.attempt_id == attempt.id,
                    DownloadArtifact.status == DownloadStatus.READY,
                )
                .order_by(DownloadArtifact.revision.desc())
                .limit(1)
            )
            if existing_artifact is not None:
                variant.download_status = DownloadStatus.READY
                if attempt.last_error_code == "DOWNLOAD_RETRYABLE":
                    clear_provider_error(attempt)
                await session.commit()
                await self._aggregate_task(session, variant.id)
                return
            validation_options = technical_validation_options(variant.generation_config_json)
            # Release the SQLite transaction before network / file work.
            await session.commit()
            try:
                async with self._download_semaphore:
                    await download_with_retries(
                        download_adapter or self.adapter,
                        url=str(result.payload["videoUrl"]),
                        destination=destination,
                    )
                async with self._validation_semaphore:
                    validation = await technical_validate(
                        destination,
                        **validation_options,
                    )
                object_key = await self.object_store.put_file(
                    destination,
                    f"users/{task.user_id}/outputs/{task.id}/{filename}",
                    "video/mp4",
                )
            except Exception:
                variant.download_status = DownloadStatus.FAILED
                variant.validation_status = ValidationStatus.FAILED
                attempt.last_error_code = "DOWNLOAD_RETRYABLE"
                attempt.updated_at = datetime.now(UTC)
                await session.commit()
                await self._aggregate_task(session, variant.id)
                return
            attempt.local_output_path = str(destination)
            artifact = DownloadArtifact(
                attempt_id=attempt.id,
                status=DownloadStatus.READY,
                object_key=object_key,
                sha256=validation.sha256,
                size_bytes=validation.size_bytes,
                duration_seconds=validation.duration_seconds,
                width=validation.width,
                height=validation.height,
                has_audio=validation.has_audio,
            )
            session.add(artifact)
            variant.download_status = DownloadStatus.READY
            variant.validation_status = (
                ValidationStatus.PASSED if validation.passed else ValidationStatus.FAILED
            )
            if validation.passed and attempt.last_error_code == "DOWNLOAD_RETRYABLE":
                clear_provider_error(attempt)
            session.add(
                ValidationResult(
                    attempt_id=attempt.id,
                    status=variant.validation_status,
                    coverage="TECHNICAL",
                    rule_version="1.0.0",
                    issue_codes_json=list(validation.issue_codes),
                    summary="Technical validation passed"
                    if validation.passed
                    else "Technical validation failed",
                )
            )
            await session.commit()
            await self._aggregate_task(session, variant.id)
        finally:
            # The attempt is already SUCCEEDED and the upstream provider already spent
            # the credit, so every exit from this function owes the reservation a
            # decision -- including the four early returns above.
            await self._finalize_attempt_billing(
                session,
                attempt,
                actual_points=actual_points,
                has_attributable_billing=has_attributable_billing,
            )
            await session.commit()

    async def _finalize_attempt_billing(
        self,
        session: AsyncSession,
        attempt: GenerationAttempt,
        *,
        actual_points: int | None,
        has_attributable_billing: bool,
    ) -> None:
        """Settle an attempt's reservation from whichever path is leaving.

        _handle_success has four early returns: a missing variant, a missing task, an
        artifact that is already READY, and a failed download. Settlement used to sit
        after the last of them, so none of those four paths ever touched the points
        that had already been reserved. Production recorded the consequence: 24 of 42
        successful attempts held UNCERTAIN reservations totalling 2880 points, and the
        ledger contained zero RELEASE entries because the release path was unreachable.

        The charge decision is delegated to attempt.charge_status rather than
        special-cased per path, because that field already encodes whether the spend
        could be attributed to a provider receipt:

        * CHARGED  -> settle at actual points, falling back to the reservation estimate
        * NOT_CHARGED -> release
        * UNKNOWN  -> park as UNCERTAIN, because an unreadable receipt must not be
          recorded as "not charged"

        settle_points is idempotent (it returns immediately unless the reservation is
        RESERVED or UNCERTAIN), so a finally block cannot double settle an attempt.
        """
        reservation = await session.scalar(
            select(PointsReservation).where(PointsReservation.attempt_id == attempt.id)
        )
        if reservation is None:
            return
        await settle_points(
            session,
            reservation,
            actual_points=(
                reservation.estimated_points if actual_points is None else int(actual_points)
            )
            if has_attributable_billing
            else None,
            charge_status=attempt.charge_status,
        )

    async def reconcile_completed_parent_phases(
        self, session: AsyncSession, *, limit: int = 100
    ) -> int:
        """Repair a missed aggregate commit, using only durable variant receipts.

        This read/write sweep does not submit, re-download, or re-edit media.
        It only advances stale GENERATING parents when *every* variant is valid.
        """
        valid = and_(
            GenerationVariant.generation_status.in_(
                (GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL)
            ),
            GenerationVariant.download_status == DownloadStatus.READY,
            GenerationVariant.validation_status.in_(
                (ValidationStatus.PASSED, ValidationStatus.WARNING)
            ),
        )
        has_variants = (
            select(GenerationVariant.id)
            .where(GenerationVariant.parent_task_id == ParentTask.id)
            .exists()
        )
        has_unfinished = (
            select(GenerationVariant.id)
            .where(GenerationVariant.parent_task_id == ParentTask.id, ~valid)
            .exists()
        )
        candidates = list(await session.scalars(
            select(ParentTask.id)
            .where(
                ParentTask.phase == ParentTaskPhase.GENERATING,
                ParentTask.updated_at < datetime.now(UTC) - timedelta(seconds=60),
                has_variants,
                ~has_unfinished,
            )
            .order_by(ParentTask.updated_at)
            .limit(limit)
        ))
        repaired = 0
        for task_id in candidates:
            task = await session.get(ParentTask, task_id)
            if task is None or task.phase != ParentTaskPhase.GENERATING:
                continue
            variants = list(await session.scalars(
                select(GenerationVariant).where(GenerationVariant.parent_task_id == task_id)
            ))
            if aggregate_parent_phase(variants) != ParentTaskPhase.DELIVERABLE:
                continue
            result = await session.execute(
                update(ParentTask)
                .where(
                    ParentTask.id == task_id,
                    ParentTask.phase == ParentTaskPhase.GENERATING,
                    ParentTask.state_version == task.state_version,
                )
                .values(
                    phase=ParentTaskPhase.DELIVERABLE,
                    phase_reason_code=None,
                    state_version=ParentTask.state_version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount == 1:
                repaired += 1
                await session.commit()
        return repaired

    async def _aggregate_task(self, session: AsyncSession, variant_id: uuid.UUID) -> None:
        variant = await session.get(GenerationVariant, variant_id)
        if variant is None:
            return
        task = await session.get(ParentTask, variant.parent_task_id)
        if task is None:
            return
        if task.phase == ParentTaskPhase.CANCELLED:
            # CANCELLED is the only terminal phase. resume_incomplete_downloads
            # selects variants by download_status without joining the task, so
            # without this guard a cancelled task could be resurrected by a stray
            # in-flight variant.
            return
        variants = list(
            await session.scalars(
                select(GenerationVariant).where(GenerationVariant.parent_task_id == task.id)
            )
        )
        next_phase = aggregate_parent_phase(variants)
        if task.phase == next_phase:
            return
        previous_phase = task.phase
        task.phase = next_phase
        task.phase_reason_code = reason_code_for_aggregate(
            task.phase_reason_code, previous_phase, next_phase
        )
        task.state_version += 1
        await session.commit()
