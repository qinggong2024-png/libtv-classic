"""Validation and immutable snapshots for the versioned multi-source CUT task."""

import hashlib
import json
import uuid

from libtv_agent.domain.enums import DownloadStatus, GenerationStatus
from libtv_agent.services.cutflow_source_validation import source_technical_ready
from libtv_agent.services.cutflow_video_type import task_video_type
from libtv_agent.domain.models import (
    Asset,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
)
from libtv_agent.services.cutflow_product_package import (
    IMAGE_SUFFIXES,
    MAX_PRODUCT_REFERENCE_BYTES,
    MAX_PRODUCT_REFERENCES,
    NATIVE_ROOT,
    native_product_folder,
    native_reference_path,
    product_reference_id,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MAX_MULTI_CUTFLOW_SOURCES = 8


class MultiCutflowError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def source_payload_hash(source_ids: list[uuid.UUID]) -> str:
    return hashlib.sha256(
        json.dumps([str(value) for value in source_ids], separators=(",", ":")).encode()
    ).hexdigest()


async def snapshot_sources(
    session: AsyncSession,
    user_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    *,
    allow_single_source: bool = False,
) -> list[dict]:
    if not source_ids:
        raise MultiCutflowError("EMPTY_SOURCES", "至少选择两条生成原片")
    if len(source_ids) < 2 and not allow_single_source:
        raise MultiCutflowError("TOO_FEW_SOURCES", "多片段剪辑至少需要两条生成原片")
    if len(source_ids) > MAX_MULTI_CUTFLOW_SOURCES:
        raise MultiCutflowError("TOO_MANY_SOURCES", f"最多选择 {MAX_MULTI_CUTFLOW_SOURCES} 条原片")
    if len(set(source_ids)) != len(source_ids):
        raise MultiCutflowError("DUPLICATE_SOURCE", "不能重复选择同一条原片")

    sources = []
    identities = set()
    for order, variant_id in enumerate(source_ids, 1):
        row = (
            await session.execute(
                select(ParentTask, GenerationVariant)
                .join(GenerationVariant, GenerationVariant.parent_task_id == ParentTask.id)
                .where(ParentTask.user_id == user_id, GenerationVariant.id == variant_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            raise MultiCutflowError("SOURCE_NOT_FOUND", "所选原片不存在或不属于当前账号")
        task, variant = row
        attempt = await session.scalar(
            select(GenerationAttempt)
            .where(
                GenerationAttempt.variant_id == variant_id,
            )
            .order_by(GenerationAttempt.created_at.desc(), GenerationAttempt.id.desc())
            .limit(1)
        )
        artifact = (
            await session.scalar(
                select(DownloadArtifact)
                .where(
                    DownloadArtifact.attempt_id == attempt.id,
                    DownloadArtifact.deleted_at.is_(None),
                    DownloadArtifact.deletion_pending_at.is_(None),
                )
                .order_by(DownloadArtifact.revision.desc())
                .limit(1)
            )
            if attempt
            else None
        )
        if (
            artifact is None
            or not artifact.object_key
            or artifact.status != DownloadStatus.READY
            or variant.generation_status
            not in {GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL}
            or not artifact.sha256
            or not artifact.size_bytes
            or not artifact.duration_seconds
        ):
            raise MultiCutflowError("SOURCE_NOT_READY", "所选原片生成、下载或技术验证尚未完成")
        if not await source_technical_ready(session, variant, attempt, artifact):
            raise MultiCutflowError("SOURCE_NOT_READY", "所选原片存在音轨以外的技术验证问题")
        if artifact.source_type.lower() in {"fake", "synthetic", "offline"} or (
            attempt and attempt.provider.lower() in {"fake", "mock", "replay"}
        ):
            raise MultiCutflowError("SYNTHETIC_MEDIA", "离线占位素材不能进入剪辑生产")
        site = (task.settings_json or {}).get("cutflowSite")
        brand_ref = site.get("brandReference") if isinstance(site, dict) else None
        # Canonical product identities do not exist in classic. A verified brand
        # snapshot is therefore the strongest comparable identity available.
        from libtv_agent.services.brand_identity import brand_identity as resolve_brand

        profile_id = (brand_ref or {}).get("profileId") or task.site_profile_id
        canonical_brand = resolve_brand(profile_id) if profile_id else None
        brand_key = (brand_ref or {}).get("accountBrandId") or (
            canonical_brand.profile_id if canonical_brand else profile_id
        )
        if not brand_key:
            raise MultiCutflowError("BRAND_IDENTITY_UNAVAILABLE", "原片缺少可核验的品牌身份")
        brand_identity = brand_key
        identities.add(brand_identity)
        # The task's own PRODUCT assets are the only real product reference we
        # have. They are shipped with the package so CUT's quality gate can run
        # against actual product images instead of a degraded brand-only path.
        product_assets = (
            (
                await session.execute(
                    select(Asset)
                    .where(
                        Asset.parent_task_id == task.id,
                        Asset.role == "PRODUCT",
                        Asset.object_key.is_not(None),
                        Asset.deleted_at.is_(None),
                    )
                    .order_by(Asset.order_no, Asset.id)
                )
            )
            .scalars()
            .all()
        )
        if not product_assets:
            raise MultiCutflowError(
                "PRODUCT_REFERENCE_REQUIRED",
                "原片缺少对应商品图，请先补齐该生成任务的商品图再送剪",
            )
        if len(product_assets) > MAX_PRODUCT_REFERENCES:
            raise MultiCutflowError(
                "PRODUCT_REFERENCE_INVALID", "每条原片最多支持五张商品图",
            )
        clip_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"classic-cut:{user_id}:{task.id}:{variant.id}:{artifact.id}",
        ))
        folder = native_product_folder(str(task.id), product_assets[0].original_filename)
        product_references = []
        for index, asset in enumerate(product_assets, 1):
            if (asset.mime_type not in IMAGE_SUFFIXES
                    or not 0 < asset.size_bytes <= MAX_PRODUCT_REFERENCE_BYTES
                    or not asset.width or not asset.height):
                raise MultiCutflowError(
                    "PRODUCT_REFERENCE_INVALID",
                    "商品图必须是有效的 JPEG、PNG 或 WebP，每张不超过 5 MiB",
                )
            suffix = IMAGE_SUFFIXES[asset.mime_type]
            ref_id = product_reference_id(clip_id, str(asset.id))
            product_references.append(
                {
                    "refId": ref_id,
                    "assetId": str(asset.id),
                    "objectKey": asset.object_key,
                    "sha256": asset.sha256,
                    "sizeBytes": asset.size_bytes,
                    "mimeType": asset.mime_type,
                    "width": asset.width,
                    "height": asset.height,
                    "filename": asset.original_filename,
                    "relativePath": f"references/{order:02d}/{ref_id}{suffix}",
                    "nativeRelativePath": native_reference_path(
                        folder, index, asset.original_filename, asset.mime_type,
                    ),
                }
            )
        sources.append(
            {
                "clipId": clip_id,
                "order": order,
                "taskId": str(task.id),
                "variantId": str(variant.id),
                "artifactId": str(artifact.id),
                "attemptId": str(attempt.id),
                "userId": str(user_id),
                "title": task.title,
                "videoType": task_video_type(task),
                "relativePath": f"sources/{order:02d}-{artifact.id}.mp4",
                "nativeProductFolder": folder,
                "nativeRelativePath": f"{NATIVE_ROOT}/{folder}/{folder}_{order:02d}.mp4",
                "sha256": artifact.sha256,
                "sizeBytes": artifact.size_bytes,
                "durationSeconds": artifact.duration_seconds,
                "width": artifact.width,
                "height": artifact.height,
                "siteProfileId": task.site_profile_id,
                "brandReference": brand_ref,
                "brandIdentity": brand_identity,
                "productIdentity": None,
                "objectKey": artifact.object_key,
                "productReferences": product_references,
            }
        )
    if len(identities) != 1:
        raise MultiCutflowError("INCOMPATIBLE_BRANDS", "多源剪辑要求所有原片属于同一已核验品牌")
    return sources


async def create_multi_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    idempotency_key: str,
    confirm_policy: bool,
    *,
    allow_single_source: bool = False,
    include_repeat_reference: bool = False,
) -> MultiCutflowTask:
    if not confirm_policy:
        raise MultiCutflowError("POLICY_CONFIRMATION_REQUIRED", "请确认剪辑策略后再提交")
    if not idempotency_key or len(idempotency_key) > 200:
        raise MultiCutflowError("INVALID_IDEMPOTENCY_KEY", "提交核对编号无效")
    digest = source_payload_hash(source_ids)
    existing = await session.scalar(
        select(MultiCutflowTask).where(
            MultiCutflowTask.user_id == user_id,
            MultiCutflowTask.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.payload_hash != digest:
            raise MultiCutflowError("IDEMPOTENCY_CONFLICT", "核对编号已用于不同的素材顺序")
        return existing
    requested_ids = {str(value) for value in source_ids}
    prior_tasks = (
        await session.scalars(
            select(MultiCutflowTask)
            .where(MultiCutflowTask.user_id == user_id)
            .order_by(MultiCutflowTask.created_at, MultiCutflowTask.id)
        )
    ).all()
    reference_task_id = None
    for prior in prior_tasks:
        prior_ids = {str(item.get("variantId")) for item in prior.sources_json}
        if prior.status in {"QUEUED", "READY", "IMPORTING", "EDITING", "UNKNOWN"} and (
            prior_ids & requested_ids or prior_ids == requested_ids
        ):
            raise MultiCutflowError(
                "SOURCE_EDIT_IN_PROGRESS",
                "所选原片正在剪辑，请等待当前剪辑结束后再重新剪辑",
            )
        if (
            include_repeat_reference
            and reference_task_id is None
            and prior.status == "PENDING_REVIEW"
            and prior.output_json
            and prior_ids == requested_ids
        ):
            reference_task_id = str(prior.id)
    sources = await snapshot_sources(
        session,
        user_id,
        source_ids,
        allow_single_source=allow_single_source,
    )
    manifest = {"sources": sources}
    if reference_task_id is not None:
        manifest["referenceBatchTaskId"] = reference_task_id
    task = MultiCutflowTask(
        user_id=user_id,
        idempotency_key=idempotency_key,
        payload_hash=digest,
        status="QUEUED",
        sources_json=sources,
        manifest_json=manifest,
    )
    try:
        async with session.begin_nested():
            session.add(task)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(MultiCutflowTask).where(
                MultiCutflowTask.user_id == user_id,
                MultiCutflowTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        if existing.payload_hash != digest:
            raise MultiCutflowError(
                "IDEMPOTENCY_CONFLICT", "核对编号已用于不同的素材顺序"
            ) from None
        return existing
    return task
