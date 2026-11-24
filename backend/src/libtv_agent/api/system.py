from functools import partial
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request
from libtv_agent.adapters.kling_official_cli import cli_model_limits
from libtv_agent.api.dependencies import Session
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import sha256_json
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_PLANNER_VERSION,
    V25_EXPERIMENTAL_VERSION,
    production_v25_enabled,
)
from libtv_agent.services.classic_admission_gate import new_video_admission_paused
from libtv_agent.services.classic_api_active_role import api_owns_background_recovery
from libtv_agent.services.classic_durable_config import acceptance_status
from libtv_agent.services.cutflow_multi_worker import multi_worker_ready
from libtv_agent.services.cutflow_protocol import JOB_CONTRACT
from libtv_agent.services.database_schema import inspect_database_schema
from libtv_agent.services.generation_safety import paid_generation_authorized
from libtv_agent.services.ootd_simple import PROFILE as OOTD_PROFILE

router = APIRouter(prefix="/system", tags=["system"])

@router.get("/classic-new-video-admission")
async def classic_new_video_admission_status(request: Request) -> dict[str, object]:
    """Safe read-only handshake for the local intake operator utility."""
    settings = request.app.state.settings
    return {
        "gateVersion": 1,
        "deploymentId": settings.deployment_id,
        "configured": settings.classic_new_video_pause_file is not None,
        "newVideoAdmissionsPaused": new_video_admission_paused(settings),
        "scope": "new-video-create-only",
    }


@router.get("/classic-durable-execution")
async def classic_durable_execution(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "contractVersion": 1,
        "deploymentId": settings.deployment_id,
        "configRevision": settings.classic_durable_config_revision,
        "analysis": acceptance_status(settings, "analysis"),
        "confirmation": acceptance_status(settings, "confirmation"),
        "analysisConcurrency": settings.classic_durable_analysis_concurrency,
    }


@router.get("/classic-api-drain")
async def classic_api_drain(request: Request) -> dict[str, object]:
    """Aggregate process-local work; no task IDs, credentials or mutations."""
    state = request.app.state
    settings = state.settings
    counts = {
        name: sum(not job.done() for job in getattr(state, name))
        for name in ("analysis_jobs", "confirmation_jobs", "subject_element_jobs",
                     "feedback_learning_jobs")
    }
    counts["http"] = state.classic_http_in_flight
    counts["recovery"] = state.classic_recovery_in_flight
    counts["imageRefresh"] = int(state.image_generation_refresh_lock.locked())
    counts["login"] = int(await state.libtv_login_manager.in_progress())
    owns_recovery = api_owns_background_recovery(settings)
    return {
        "contractVersion": 1,
        "recoveryLockVersion": 1,
        "deploymentId": settings.deployment_id,
        "port": settings.app_port,
        "sourceRevision": settings.deployment_source_revision,
        "ownsRecovery": owns_recovery,
        "workCounts": counts,
        "durableAnalysis": settings.classic_durable_analysis_enabled,
        "durableConfirmation": settings.classic_durable_confirmation_enabled,
        "retirementReady": (
            settings.classic_api_route_file is not None and not owns_recovery
            and all(count == 0 for count in counts.values())
        ),
    }


def _runtime_version() -> str:
    try:
        return version("libtv-production-agent")
    except PackageNotFoundError:
        return "0.1.0"


def _config_revision(settings: Settings) -> str:
    return sha256_json(
        {
            "environment": settings.app_env,
            "deploymentId": settings.deployment_id,
            "aiAdapterMode": settings.ai_adapter_mode,
            "ootdV1Enabled": settings.ai_ootd_v1_enabled,
            "libtvAdapterMode": settings.libtv_adapter_mode,
            "libtvExecutionMode": settings.libtv_execution_mode,
            "paidGenerationEnabled": settings.paid_generation_enabled,
            "paidSubmissionsPaused": settings.paid_submissions_paused,
            "generationProvider": settings.active_kling_provider_name,
            "klingProviderBackend": settings.kling_provider_backend,
            "klingModel": settings.active_kling_model,
            "klingFallbackMode": settings.kling_storyboard_fallback_mode,
            "downloadConcurrency": settings.download_concurrency,
            "technicalValidationConcurrency": settings.technical_validation_concurrency,
            "outputRetentionDays": settings.output_retention_days,
            **({"durableAnalysis": True} if settings.classic_durable_analysis_enabled else {}),
            **({"durableConfirmation": True}
               if settings.classic_durable_confirmation_enabled else {}),
            **(
                {"durableConfigRevision": settings.classic_durable_config_revision}
                if settings.classic_durable_analysis_enabled
                or settings.classic_durable_confirmation_enabled else {}
            ),
        }
    )


@router.get("/status")
async def system_status(request: Request, session: Session) -> dict[str, object]:
    settings = request.app.state.settings
    connection = await session.connection()
    schema = await connection.run_sync(
        partial(
            inspect_database_schema,
            optional_tables=(
                frozenset()
                if getattr(request.app.state.settings, "cutflow_handoff_enabled", False)
                else frozenset({"cutflow_handoff"})
            ),
        )
    )
    schema_revision = ",".join(schema.current_revisions)
    if not schema_revision:
        schema_revision = f"metadata-equivalent:{','.join(schema.expected_revisions)}"
    kling_availability = request.app.state.kling_adapter.availability_snapshot()
    kling_capabilities = request.app.state.kling_adapter.capabilities_snapshot()
    standard_video_durations = [str(value) for value in range(3, 16)]
    capability_defaults = {
        "api": {
            "imageInputLimit": 7,
            "supportsMultipleImages": True,
            "videoDurations": standard_video_durations,
            "tailImageVideoDurations": standard_video_durations,
        },
        "wavespeed": {
            "imageInputLimit": 7,
            "supportsMultipleImages": True,
            "videoDurations": standard_video_durations,
            "tailImageVideoDurations": standard_video_durations,
        },
        "bach": {
            "imageInputLimit": 2,
            "supportsMultipleImages": False,
            "videoDurations": [str(value) for value in range(1, 9)],
            "tailImageVideoDurations": [str(value) for value in range(1, 7)],
        },
    }
    active_defaults = capability_defaults.get(
        settings.kling_provider_backend,
        {
            "imageInputLimit": 0,
            "supportsMultipleImages": False,
            "videoDurations": standard_video_durations,
            "tailImageVideoDurations": standard_video_durations,
        },
    )
    active_model = settings.active_kling_model
    cli_limits = (
        cli_model_limits(kling_capabilities, active_model)
        if settings.kling_provider_backend == "cli" and kling_capabilities
        else None
    )
    video_generation = {
        "enabled": settings.kling_enabled,
        "backend": settings.kling_provider_backend,
        "provider": request.app.state.kling_adapter.get_provider_name(),
        "model": active_model,
        "status": kling_availability.status,
        "available": kling_availability.available and (
            settings.kling_provider_backend != "cli" or cli_limits is not None
        ),
        "authenticated": kling_availability.authenticated,
        "reason": (
            "当前 CLI 模型能力尚未确认，请管理员刷新可灵连接"
            if settings.kling_provider_backend == "cli" and cli_limits is None
            else kling_availability.reason
        ),
        "cliVersion": kling_availability.cli_version,
        "models": (
            list(kling_capabilities.supported_models)
            if kling_capabilities
            else ([active_model] if active_model else [])
        ),
        "supportsMultipleImages": (
            cli_limits["supportsMultipleImages"] if cli_limits is not None
            else kling_capabilities.supports_multiple_images
            if settings.kling_provider_backend != "cli" and kling_capabilities
            else active_defaults["supportsMultipleImages"]
        ),
        "imageInputLimit": (
            cli_limits["imageInputLimit"] if cli_limits is not None
            else kling_capabilities.image_input_limit
            if settings.kling_provider_backend != "cli" and kling_capabilities
            else active_defaults["imageInputLimit"]
        ),
        "videoDurations": (
            cli_limits["videoDurations"] if cli_limits is not None
            else list(kling_capabilities.video_durations)
            if (settings.kling_provider_backend != "cli" and kling_capabilities
                and kling_capabilities.video_durations)
            else active_defaults["videoDurations"]
        ),
        "tailImageVideoDurations": (
            cli_limits["tailImageVideoDurations"] if cli_limits is not None
            else list(kling_capabilities.tail_image_video_durations)
            if (settings.kling_provider_backend != "cli" and kling_capabilities
                and kling_capabilities.tail_image_video_durations)
            else (
                list(kling_capabilities.video_durations)
                if (settings.kling_provider_backend != "cli" and kling_capabilities
                and kling_capabilities.video_durations)
                else active_defaults["tailImageVideoDurations"]
            )
        ),
        "supportsElementReferences": (
            cli_limits["supportsElementReferences"] if cli_limits is not None
            else settings.kling_provider_backend != "cli"
        ),
        "supportsProductReferences": (
            cli_limits["supportsProductReferences"] if cli_limits is not None
            else settings.kling_provider_backend != "cli"
        ),
        "fallbackMode": settings.kling_storyboard_fallback_mode,
    }
    return {
        "environment": settings.app_env,
        "sourceRevision": settings.deployment_source_revision,
        "schemaRevision": schema_revision,
        "configRevision": _config_revision(settings),
        "runtimeVersion": _runtime_version(),
        "libtvAdapter": settings.libtv_adapter_mode,
        "libtvExecutionMode": settings.libtv_execution_mode,
        "paidGenerationEnabled": settings.paid_generation_enabled,
        "paidGenerationAuthorized": paid_generation_authorized(settings),
        "paidGenerationBoundary": "libtv node <nodeKey> --project <projectUuid> --run",
        "openaiModel": settings.openai_model,
        "openaiConfigured": bool(settings.openai_api_key),
        "libtvConfigured": bool(settings.libtv_access_key),
        "outputRetentionDays": settings.output_retention_days,
        "downloadConcurrency": settings.download_concurrency,
        "technicalValidationConcurrency": settings.technical_validation_concurrency,
        "zipEnabled": False,
        "phaseOneUserId": settings.dev_user_id
        if settings.app_env in {"development", "test"}
        else None,
        "phaseOneAdminId": settings.dev_admin_id
        if settings.app_env in {"development", "test"}
        else None,
        "sharedAuthentication": settings.shared_authentication,
        "csrfCookieName": settings.csrf_cookie_name,
        "cutflowMultiSourceReady": multi_worker_ready(settings),
        "cutflowContractVersion": JOB_CONTRACT,
        "cutflowStatusAuthority": (
            "nas-status" if settings.cutflow_nas_status_enabled else "legacy"
        ),
        "aiAdapter": settings.ai_adapter_mode,
        "creativePlanner": {
            "version": (
                OOTD_PROFILE if settings.ai_ootd_v1_enabled
                else V25_EXPERIMENTAL_VERSION if production_v25_enabled(settings)
                else CREATIVE_PLANNER_VERSION
            ),
            "v25Enabled": not settings.ai_ootd_v1_enabled and production_v25_enabled(settings),
            **({
                "ootdV1Enabled": True,
                "rollout": "all-accounts",
                "uspEnabled": False,
            } if settings.ai_ootd_v1_enabled else {}),
        },
        "videoGeneration": video_generation,
        # Retained for older frontends while they migrate to the provider-neutral field.
        "kling": video_generation,
    }
