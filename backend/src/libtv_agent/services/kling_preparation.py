from __future__ import annotations

from pathlib import Path
from typing import Any

from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.video_provider import (
    VideoElementReference,
    VideoGenerationRequest,
    VideoProviderAdapter,
)
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import sha256_json
from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase
from libtv_agent.domain.models import GenerationAttempt, ParentTask
from libtv_agent.services.ootd_director_memory import (
    MemoryDestination,
    memory_hint_text,
    runtime_memory,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


def _asset_suffix(filename: str, object_key: str | None = None) -> str:
    # Upload preprocessing stores normalized JPEG bytes under normalized.jpg,
    # while original_filename intentionally retains the user's source name.
    # Prefer the stored object's suffix so a WebP upload is not localized as
    # JPEG bytes behind a misleading .webp extension.
    for candidate in (object_key, filename):
        suffix = Path(candidate or "").suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png"}:
            return suffix
    return ".jpg"


def _resolve_multi_shot(config: dict[str, Any], settings: Settings) -> bool:
    multi_shot = config.get("multiShot")
    if isinstance(multi_shot, bool):
        return multi_shot
    smart_storyboard = config.get("smartStoryboard")
    if isinstance(smart_storyboard, bool):
        return smart_storyboard
    return settings.active_video_multi_shot


def _resolve_element_references(config: dict[str, Any]) -> tuple[VideoElementReference, ...]:
    value = config.get("elementIds")
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise KlingCliError("KLING_INVALID_INPUT", "elementIds must be an array")
    element_ids = tuple(str(item).strip() for item in value)
    if any(not element_id for element_id in element_ids):
        raise KlingCliError("KLING_INVALID_INPUT", "elementIds cannot contain empty values")
    if len(element_ids) != len(set(element_ids)):
        raise KlingCliError("KLING_INVALID_INPUT", "elementIds must be unique")
    return tuple(
        VideoElementReference(element_id=element_id, content_id=f"element_{index}")
        for index, element_id in enumerate(element_ids, start=1)
    )


async def prepare_kling_task(
    session: AsyncSession,
    adapter: VideoProviderAdapter,
    object_store: ObjectStore,
    task: ParentTask,
    settings: Settings,
    *,
    persist_prepared: bool = True,
) -> list[GenerationAttempt]:
    # Direct admission owns the final transaction; manual confirmation retains
    # its existing durable preparation checkpoint until separately migrated.
    expected_version = task.state_version
    expected_phase = task.phase
    availability = await adapter.check_availability()
    if not availability.available:
        raise KlingCliError(
            availability.status,
            availability.reason or "Kling provider is unavailable",
        )
    storyboard_assets = sorted(
        (asset for asset in task.assets if asset.used_for_generation
         and asset.role not in {"PRODUCT", "REFERENCE_VIDEO", "BACKGROUND_REFERENCE"}),
        key=lambda item: item.order_no,
    )
    product_reference_assets = sorted(
        (asset for asset in task.assets if asset.used_for_generation
         and asset.role in {"PRODUCT", "BACKGROUND_REFERENCE"}),
        key=lambda item: item.order_no,
    )
    video_assets = [asset for asset in task.assets if asset.role == "REFERENCE_VIDEO"]
    reference_video_path = None
    if video_assets:
        video = video_assets[0]
        if len(video_assets) != 1 or not video.object_key:
            raise KlingCliError("WAVESPEED_INPUT_INVALID", "需要一条有效参考视频")
        suffix = ".mov" if video.mime_type == "video/quicktime" else ".mp4"
        reference_video_path = await object_store.localize(
            video.object_key, settings.runner_root / "kling" / "inputs"
            / str(task.id) / f"reference{suffix}",
        )
    if not storyboard_assets and not reference_video_path:
        raise KlingCliError("KLING_INVALID_INPUT", "At least one storyboard image is required")
    storyboard_paths: list[Path] = []
    for index, asset in enumerate(storyboard_assets, start=1):
        if not asset.object_key:
            raise KlingCliError(
                "KLING_INVALID_INPUT", f"Storyboard asset {asset.id} has no source object"
            )
        storyboard_paths.append(
            await object_store.localize(
                asset.object_key,
                settings.runner_root
                / "kling"
                / "inputs"
                / str(task.id)
                / f"storyboard-{index}{_asset_suffix(asset.original_filename, asset.object_key)}",
            )
        )
    product_reference_paths: list[Path] = []
    for index, asset in enumerate(product_reference_assets, start=1):
        if not asset.object_key:
            raise KlingCliError(
                "KLING_INVALID_INPUT", f"Product reference asset {asset.id} has no source object"
            )
        product_reference_paths.append(
            await object_store.localize(
                asset.object_key,
                settings.runner_root
                / "kling"
                / "inputs"
                / str(task.id)
                / (
                    f"product-reference-{index}"
                    f"{_asset_suffix(asset.original_filename, asset.object_key)}"
                ),
            )
        )

    variant_ids = [variant.id for variant in task.variants]
    existing = list(
        await session.scalars(
            select(GenerationAttempt)
            .where(GenerationAttempt.variant_id.in_(variant_ids))
            .order_by(GenerationAttempt.created_at.desc())
        )
    )
    latest_by_variant: dict[Any, GenerationAttempt] = {}
    for attempt in existing:
        latest_by_variant.setdefault(attempt.variant_id, attempt)

    attempts: list[GenerationAttempt] = []
    staged_changes = []
    provider_name = adapter.get_provider_name()
    task_settings = task.settings_json if isinstance(task.settings_json, dict) else {}
    for variant in sorted(
        task.variants,
        key=lambda item: item.variant_index or (1 if item.variant_type.value == "STABLE" else 2),
    ):
        duration = int(
            variant.generation_config_json.get("duration")
            or variant.generation_config_json.get("durationSeconds")
            or settings.active_kling_duration
        )
        aspect_ratio = str(
            variant.generation_config_json.get("aspectRatio") or settings.active_kling_aspect_ratio
        )
        multi_shot = _resolve_multi_shot(variant.generation_config_json, settings)
        audio = str(
            variant.generation_config_json.get("audio")
            or variant.generation_config_json.get("sound")
            or settings.active_video_audio
        )
        element_references = _resolve_element_references(variant.generation_config_json)
        # Freeze reusable execution memory BEFORE validation and persist the
        # result with this variant. Later file updates cannot silently change
        # the provider prompt during retries or unknown-result reconciliation.
        saved_hint = variant.generation_config_json.get("directorMemoryKlingHint")
        pending = latest_by_variant.get(variant.id)
        is_existing_submission = pending is not None and pending.status in {
            AttemptStatus.PREPARED, AttemptStatus.DISPATCH_PENDING,
            AttemptStatus.LAUNCHING, AttemptStatus.RUNNING,
            AttemptStatus.RECONCILING, AttemptStatus.UNKNOWN_AFTER_SUBMIT,
            AttemptStatus.SUCCEEDED,
        }
        memory_hint = ""
        if isinstance(saved_hint, str):
            memory_hint = saved_hint
        elif (not is_existing_submission
              and settings.kling_provider_backend == "api"
              and settings.active_kling_model == "kling-3.0-omni"
              and task.workflow_kind != "DEEP_VIDEO"
              and str(variant.generation_config_json.get("directorNativeProfile", "")).startswith(
                  "allwe-ootd-sol-"
              )):
            selection = runtime_memory(
                brand=(str(task_settings.get("directorMemoryBrand") or
                           task.site_profile_id or "").lower()),
                destination=MemoryDestination.KLING,
                director_mode=(
                    "reference_guided" if
                    variant.generation_config_json.get("directorNativeProfile")
                    == "allwe-ootd-sol-simple-v1" else "cross_scene_v2"
                ),
                garment_category=(
                    task_settings.get("garmentCategory")
                    if isinstance(task_settings.get("garmentCategory"), str)
                    else None
                ),
            )
            memory_hint = memory_hint_text(selection, destination=MemoryDestination.KLING)
        request = VideoGenerationRequest(
            prompt=variant.prompt_text,
            director_native_profile=variant.generation_config_json.get("directorNativeProfile"),
            director_memory_brand=(str(task_settings.get("directorMemoryBrand") or
                                        task.site_profile_id or "").lower()),
            director_memory_garment_category=(
                task_settings.get("garmentCategory")
                if isinstance(task_settings.get("garmentCategory"), str)
                else None
            ),
            director_memory_kling_hint=(memory_hint or None),
            model=(variant.generation_config_json.get("model")
                   if task.workflow_kind == "DEEP_VIDEO" else settings.active_kling_model),
            image_paths=tuple(storyboard_paths),
            duration=duration,
            aspect_ratio=aspect_ratio,
            product_reference_paths=tuple(product_reference_paths),
            fallback_mode=settings.kling_storyboard_fallback_mode,
            task_trace_id=task.id.hex,
            resolution=settings.active_video_resolution,
            audio=audio,
            multi_shot=multi_shot,
            external_task_id=f"{task.id.hex}-{variant.id.hex}-{variant.revision}",
            callback_url=(
                settings.kling_api_callback_url
                if settings.kling_provider_backend != "bach"
                else None
            ),
            element_references=element_references,
            reference_video_path=reference_video_path,
        )
        model = await adapter.validate_request(request)
        intent_hash = sha256_json(
            {
                "userId": str(task.user_id),
                "parentTaskId": str(task.id),
                "childTaskType": variant.variant_key or variant.variant_type.value,
                "variantIndex": variant.variant_index,
                "provider": provider_name,
                "model": model,
                "inputAssetHash": sha256_json(
                    {
                        "storyboards": [asset.sha256 for asset in storyboard_assets],
                        "productReferences": [asset.sha256 for asset in product_reference_assets],
                        "referenceVideos": [asset.sha256 for asset in video_assets],
                    }
                ),
                "promptHash": variant.prompt_hash,
                **({"directorMemoryKlingHintSha256": sha256_json(memory_hint)}
                   if memory_hint else {}),
                "duration": duration,
                "aspectRatio": aspect_ratio,
                "resolution": settings.kling_api_resolution,
                "audio": audio,
                "multiShot": multi_shot,
                "elementIds": [item.element_id for item in element_references],
            }
        )
        attempt = latest_by_variant.get(variant.id)
        if attempt is None or attempt.status not in {
            AttemptStatus.PREPARED,
            AttemptStatus.DISPATCH_PENDING,
            AttemptStatus.LAUNCHING,
            AttemptStatus.RUNNING,
            AttemptStatus.RECONCILING,
            AttemptStatus.UNKNOWN_AFTER_SUBMIT,
            AttemptStatus.SUCCEEDED,
        }:
            attempt = GenerationAttempt(
                variant_id=variant.id,
                provider=provider_name,
                provider_model=model,
                provider_status="queued",
                intent_hash=intent_hash,
                submission_key=intent_hash,
                status=AttemptStatus.PREPARED,
            )
        else:
            if attempt.intent_hash != intent_hash:
                raise KlingCliError(
                    "KLING_SUBMIT_RESULT_UNKNOWN",
                    "An active Kling submission already exists for this variant",
                    submitted=True,
                    uncertain=True,
                )
        if not persist_prepared and attempt.status != AttemptStatus.PREPARED:
            raise KlingCliError(
                "KLING_SUBMIT_RESULT_UNKNOWN",
                "Existing attempt is no longer eligible for direct queue admission",
                submitted=True, uncertain=True,
            )
        config = {
            **variant.generation_config_json,
            "generationProvider": provider_name,
            "model": model,
            "modelKey": model,
            "duration": duration,
            "aspectRatio": aspect_ratio,
            "resolution": settings.active_video_resolution,
            "audio": audio,
            "multiShot": multi_shot,
            "elementIds": [item.element_id for item in element_references],
            "elementReferenceCount": len(element_references),
            "providerBackend": (
                "wavespeed" if reference_video_path else settings.kling_provider_backend
            ),
            "providerName": provider_name,
            "storyboardFallbackMode": settings.kling_storyboard_fallback_mode,
            "storyboardImageCount": len(storyboard_assets),
            "productReferenceImageCount": len(product_reference_assets),
            "imageRolePolicy": "storyboard_ordered_product_primary_supplementary",
            **({"directorMemoryKlingHint": memory_hint}
               if memory_hint else {}),
            "referenceImageBindings": [
                {
                    "imageId": f"image_{index}",
                    "role": "STORYBOARD" if index <= len(storyboard_assets) else "PRODUCT",
                    "assetId": str(asset.id),
                    "sha256": asset.sha256,
                }
                for index, asset in enumerate(
                    [*storyboard_assets, *product_reference_assets], start=1
                )
            ],
        }
        staged_changes.append((variant, attempt, config))
        attempts.append(attempt)
    # All provider validation and file localization finished before any of
    # these ORM mutations can flush. Fence concurrent cancellation/admission.
    if not persist_prepared:
        if expected_phase != ParentTaskPhase.PREPARING:
            raise RuntimeError("Direct admission requires PREPARING")
        claimed = await session.execute(
            update(ParentTask)
            .where(
                ParentTask.id == task.id,
                ParentTask.phase == expected_phase,
                ParentTask.state_version == expected_version,
            )
            .values(state_version=ParentTask.state_version)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            raise RuntimeError("Direct admission ownership changed")
    for variant, attempt, config in staged_changes:
        session.add(attempt)
        variant.generation_config_json = config
    if persist_prepared:
        task.phase = ParentTaskPhase.QUEUED
        task.state_version += 1
        await session.commit()
    else:
        # IDs are available for fee reservation and command creation, but
        # nothing is committed until the caller's single admission commit.
        await session.flush()
    return attempts
