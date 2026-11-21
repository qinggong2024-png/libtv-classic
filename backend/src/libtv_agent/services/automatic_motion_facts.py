"""Reuse analysis configuration and visual cache without a human approval step."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from libtv_agent.adapters.motion_facts import (
    MOTION_FACT_VERSION,
    ImageMotionFacts,
    OpenAIMotionFactsAdapter,
)
from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.domain.contracts import sha256_json
from libtv_agent.domain.models import StagedUpload, VisualFactCache
from libtv_agent.services.analysis import (
    _analysis_image_url,
    _cache_entry_is_current,
    _store_visual_fact_cache,
)
from libtv_agent.services.generation_safety import paid_generation_authorized
from libtv_agent.templates.facial_expressions import FaceEvidence
from libtv_agent.templates.garment_materials import GarmentMaterial
from libtv_agent.templates.motion_actions import ActionContext
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ObservedMotion:
    context: ActionContext = ActionContext()
    source: str = "unknown"
    reason: str | None = None
    face_evidence: FaceEvidence = FaceEvidence()
    garment_material: GarmentMaterial = "unknown"


async def observe_storyboards(
    session: AsyncSession,
    *,
    uploads: list[StagedUpload],
    user_id: uuid.UUID,
    brand_id: uuid.UUID | None,
    settings: Settings,
    object_store: ObjectStore,
) -> list[ObservedMotion]:
    # Never promote a fake/replay/demo installation to a paid image call.
    if (
        settings.ai_adapter_mode != "openai"
        or not settings.openai_api_key
        or not paid_generation_authorized(settings)
    ):
        return [ObservedMotion(reason="analysis_disabled") for _ in uploads]
    now = datetime.now(UTC)
    results: dict[str, ObservedMotion] = {}
    missing: dict[str, StagedUpload] = {}
    keys = [
        sha256_json(
            {
                "user": str(user_id),
                "brand": str(brand_id) if brand_id else None,
                "image": upload.sha256,
                "version": MOTION_FACT_VERSION,
                "model": settings.openai_model,
                "endpoint": settings.openai_base_url,
            }
        )
        for upload in uploads
    ]
    for key, upload in zip(keys, uploads, strict=True):
        cached = await session.get(VisualFactCache, key)
        if cached and _cache_entry_is_current(cached, now):
            try:
                facts = ImageMotionFacts.model_validate(cached.facts_json)
                results[key] = ObservedMotion(
                    facts.context(),
                    "automatic_vision_cache",
                    face_evidence=facts.face_evidence(),
                    garment_material=facts.garment_material(),
                )
                continue
            except ValueError:
                pass
        missing[key] = upload
    if missing:
        try:

            async def prepare(key: str, upload: StagedUpload) -> AnalysisImageInput:
                url = await _analysis_image_url(
                    object_store,
                    upload.object_key,
                    str(upload.id),
                    settings.artifact_root,
                )
                return AnalysisImageInput(asset_id=key, order=1, image_url=url)

            # One bounded request for all cache misses; facts are still isolated by image ID.
            async with asyncio.timeout(55):
                images = await asyncio.gather(*(prepare(k, u) for k, u in missing.items()))
                response = await OpenAIMotionFactsAdapter(settings).analyze(list(images))
            for facts in response.images:
                results[facts.asset_id] = ObservedMotion(
                    facts.context(),
                    "automatic_vision",
                    face_evidence=facts.face_evidence(),
                    garment_material=facts.garment_material(),
                )
        except Exception:
            # No raw provider error or image data in logs/metadata. Do not cache failures.
            results.update({key: ObservedMotion(reason="analysis_unavailable") for key in missing})
        else:
            for facts in response.images:
                await _store_visual_fact_cache(
                    session,
                    cache_key=facts.asset_id,
                    ordered_storyboard_hash=missing[facts.asset_id].sha256,
                    model_version=settings.openai_model[:80],
                    schema_version=MOTION_FACT_VERSION,
                    facts_json=facts.model_dump(mode="json"),
                    expires_at=now + timedelta(days=30),
                )
    return [results[key] for key in keys]
