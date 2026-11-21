"""Durable NAS handoffs. HTTP requests enqueue; a separate worker transfers media."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.config import Settings
from libtv_agent.domain.enums import DownloadStatus, GenerationStatus, ValidationStatus
from libtv_agent.domain.models import (
    Asset,
    CutflowHandoff,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    ValidationResult,
)
from libtv_agent.services.content_catalog import get_merged_site_style_profile
from libtv_agent.services.cutflow_policy import (
    AUTO_CONTRACT,
    LAN_CONTRACT,
    POLICY_VERSION,
    automatic_policy,
    lan_policy,
    saved_policy,
)
from libtv_agent.services.cutflow_references import snapshot_references, validate_reference_snapshot
from libtv_agent.services.cutflow_source_validation import source_technical_ready
from libtv_agent.services.cutflow_video_type import task_video_type
from libtv_agent.services.cutflow_site import lock_source_task, site_choices
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

CONTRACT = "libtv-to-cutflow/1.1"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


class CutflowHandoffError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def configured(settings: Settings) -> bool:
    return bool(
        settings.cutflow_handoff_enabled
        and settings.cutflow_handoff_root
        and settings.cutflow_base_url
        and (settings.cutflow_transport == "lan" or settings.cutflow_service_token)
    )


def require_configuration(settings: Settings) -> None:
    if not configured(settings):
        raise CutflowHandoffError("CUTFLOW_UNAVAILABLE", "Cutflow 交接服务尚未配置")
    parsed = urlsplit(settings.cutflow_base_url or "")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise CutflowHandoffError("CUTFLOW_UNAVAILABLE", "Cutflow 服务地址配置无效")


def site_identity(site_url: str) -> dict[str, str]:
    """Assign a stable UUID v5 to a real catalog storefront, never to this server."""
    if not isinstance(site_url, str):
        raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "商品站点缺少有效正式域名")
    parsed = urlsplit(site_url)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or "." not in host
        or parsed.username
        or parsed.password
        or parsed.netloc.lower() != host
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
        or ".." in host
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "商品站点缺少有效正式域名")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "不能将服务器 IP 作为商品站点")
    canonical_url = f"https://{host}/"
    return {
        "site_id": str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_url)), "site_url": canonical_url,
    }


def lan_receipt_accepted(handoff: CutflowHandoff) -> bool:
    """DB-only acceptance marker; never part of the immutable NAS manifest."""
    payload = handoff.manifest_json
    digest = payload.get("manifestSha256") if isinstance(payload, dict) else None
    return (handoff.contract_version == LAN_CONTRACT and isinstance(digest, str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
            and payload.get("_acceptedManifestSha256") == digest)


def handoff_identity(handoff: CutflowHandoff) -> dict[str, str]:
    """Fence old packages and inconsistent identity before any NAS/HTTP work."""
    if handoff.contract_version not in {CONTRACT, AUTO_CONTRACT, LAN_CONTRACT}:
        raise CutflowHandoffError("CUTFLOW_CONTRACT_UNSUPPORTED", "交接协议版本不受支持")
    payload = handoff.manifest_json
    if not isinstance(payload, dict):
        raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "交接身份格式无效")
    try:
        saved_policy(handoff)
    except (ValueError, TypeError, AttributeError):
        raise CutflowHandoffError("CUTFLOW_POLICY_INVALID", "自动剪辑策略快照无效") from None
    source = payload if handoff.manifest_path else payload.get("source", {})
    if not isinstance(source, dict):
        raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "交接身份格式无效")
    user_id = source.get("userId" if handoff.manifest_path else "user_id")
    site_id = source.get("siteId" if handoff.manifest_path else "site_id")
    if handoff.contract_version == LAN_CONTRACT:
        if user_id != str(handoff.user_id) or (
            handoff.manifest_path and payload.get("handoffId") != handoff.handoff_key
        ):
            raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "交接任务标识不匹配")
        if handoff.manifest_path and not re.fullmatch(
            r"[0-9a-f]{64}", str(payload.get("manifestSha256", "")),
        ):
            raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "已发布交接缺少有效哈希")
        return {"userId": user_id}
    try:
        valid_site = uuid.UUID(site_id)
        valid_user = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        raise CutflowHandoffError(
            "CUTFLOW_IDENTITY_INVALID", "交接缺少有效站点或用户 UUID"
        ) from None
    if (
        str(valid_site) != site_id
        or str(valid_user) != user_id
        or valid_site.int == 0
        or valid_user.int == 0
        or valid_user != handoff.user_id
        or (handoff.manifest_path and payload.get("handoffId") != handoff.handoff_key)
    ):
        raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "交接站点或用户归属不匹配")
    if handoff.manifest_path:
        digest = payload.get("manifestSha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "已发布交接缺少有效哈希")
    if not handoff.manifest_path:
        if site_identity(source.get("site_url", ""))["site_id"] != site_id:
            raise CutflowHandoffError("CUTFLOW_IDENTITY_INVALID", "交接站点域名与 UUID 不一致")
    return {"userId": user_id, "siteId": site_id}


def safe_path(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or "\\" in relative
        or ":" in relative
        or relative.startswith("/")
        or any(p in {"", ".", ".."} for p in relative.split("/"))
    ):
        raise CutflowHandoffError("UNSAFE_PATH", "交接文件路径无效")
    canonical = root.resolve()
    result = (canonical / relative).resolve()
    if not result.is_relative_to(canonical) or result == canonical:
        raise CutflowHandoffError("UNSAFE_PATH", "交接文件路径越界")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_nas_status(handoff: CutflowHandoff, settings: Settings) -> dict | None:
    """Read only this published /2 job; the worker applies the HTTP validators."""
    if (not settings.cutflow_nas_status_enabled or settings.cutflow_transport != "lan"
            or handoff.contract_version != LAN_CONTRACT or not handoff.manifest_path
            or saved_policy(handoff)["version"] != POLICY_VERSION):
        return None
    path = safe_path(
        Path(settings.cutflow_handoff_root), f"outbox/{handoff.handoff_key}/status.json",
    )
    try:
        with path.open("rb") as handle:
            raw = handle.read(1_048_577)
    except OSError:
        # A missing/offline share must not prevent the HTTP notification/query.
        return None
    if len(raw) > 1_048_576:
        raise CutflowHandoffError("INVALID_RESULT", "NAS 剪辑状态回执过大")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CutflowHandoffError("INVALID_RESULT", "NAS 剪辑状态回执格式错误") from None
    if not isinstance(result, dict):
        raise CutflowHandoffError("INVALID_RESULT", "NAS 剪辑状态回执格式错误")
    return result


def copy_verified(source: Path, destination: Path, expected: str, max_bytes: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4()}.part")
    try:
        size = 0
        digest = hashlib.sha256()
        with source.open("rb") as src, partial.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise CutflowHandoffError("MEDIA_TOO_LARGE", "视频超过交接大小上限")
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if size == 0 or digest.hexdigest() != expected or sha256_file(partial) != expected:
            raise CutflowHandoffError("VIDEO_HASH_MISMATCH", "视频文件哈希校验失败")
        os.replace(partial, destination)
        return size
    finally:
        partial.unlink(missing_ok=True)


def write_json_atomic(destination: Path, payload: dict[str, Any]) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4()}.part")
    try:
        with partial.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


async def create_cutflow_handoff(
    session: AsyncSession,
    *,
    settings: Settings,
    object_store: ObjectStore,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    variant_id: uuid.UUID,
) -> CutflowHandoff:
    require_configuration(settings)
    await lock_source_task(session, user_id, task_id)
    row = (
        await session.execute(
            select(ParentTask, GenerationVariant)
            .join(GenerationVariant, GenerationVariant.parent_task_id == ParentTask.id)
            .where(
                ParentTask.id == task_id,
                ParentTask.user_id == user_id,
                GenerationVariant.id == variant_id,
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise CutflowHandoffError("TASK_NOT_FOUND", "视频结果不存在")
    task, variant = row
    attempt = await session.scalar(
        select(GenerationAttempt)
        .where(GenerationAttempt.variant_id == variant_id)
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
        or variant.generation_status not in {GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL}
    ):
        raise CutflowHandoffError("VIDEO_NOT_READY", "视频生成、下载或技术校验尚未完成")
    if not await source_technical_ready(session, variant, attempt, artifact):
        raise CutflowHandoffError("VIDEO_NOT_READY", "视频生成、下载或技术校验尚未完成")
    if (
        artifact.source_type.lower() in {"fake", "synthetic", "offline"}
        or (artifact.object_key.startswith(("fake:", "offline:", "synthetic:")))
        or attempt.provider.lower() in {"fake", "replay", "mock"}
    ):
        raise CutflowHandoffError("SYNTHETIC_MEDIA", "离线占位素材不能进入剪辑生产")
    existing_query = select(CutflowHandoff).where(
        CutflowHandoff.user_id == user_id,
        CutflowHandoff.artifact_id == artifact.id,
    )
    existing = await session.scalar(existing_query)
    if existing:
        handoff_identity(existing)
        return existing
    if settings.cutflow_transport == "lan":
        # Optional business metadata, never a cross-machine authorization gate.
        site = {}
        site_profile_id = task.site_profile_id
        if task.site_profile_id:
            site["site_profile_id"] = task.site_profile_id
    else:
        if not task.site_profile_id or not task.site_profile_version:
            raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "视频任务没有已确认的商品站点")
        snapshot = (task.settings_json or {}).get("cutflowSite")
        if snapshot is not None and not isinstance(snapshot, dict):
            raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "原任务站点快照无效")
        choices, _default = await site_choices(session, user_id)
        try:
            profile = await get_merged_site_style_profile(
                session, task.site_profile_id, require_active=True,
            )
        except ValueError:
            raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "视频任务商品站点不可用") from None
        if profile.id not in {item.id for item in choices}:
            raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "站点不属于当前账号品牌")
        if profile.library_scope != "brand" or (
            not snapshot and profile.version != task.site_profile_version
        ):
            raise CutflowHandoffError(
                "CUTFLOW_SITE_REQUIRED", "视频任务站点版本与当前已确认记录不符"
            )
        if snapshot:
            if (snapshot.get("userId") != str(user_id)
                    or snapshot.get("profileId") != task.site_profile_id
                    or snapshot.get("profileVersion") != task.site_profile_version
                    or snapshot.get("siteUrl") != site_identity(profile.site_url)["site_url"]):
                raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "原任务站点快照与归属不一致")
            site = site_identity(snapshot.get("siteUrl"))
            if site["site_id"] != snapshot.get("siteId"):
                raise CutflowHandoffError("CUTFLOW_SITE_REQUIRED", "原任务站点快照无效")
        else:
            site = site_identity(profile.site_url)
        site_profile_id = profile.id
    snapshot = (task.settings_json or {}).get("cutflowSite")
    if (isinstance(snapshot, dict) and snapshot.get("profileId") == task.site_profile_id
            and snapshot.get("userId") == str(user_id)
            and isinstance(snapshot.get("brandReference"), dict)):
        site["brand_reference"] = snapshot["brandReference"]
    # Classic has no canonical Product/director model; do not invent a product ID.
    context = {}
    product_id = None
    policy = None
    references = None
    automatic_checks = []
    if settings.cutflow_auto_edit_enabled or settings.cutflow_transport == "lan":
        try:
            policy = (lan_policy() if settings.cutflow_transport == "lan"
                      else automatic_policy(settings.cutflow_ootd_template_id or "", user_id))
        except (ValueError, TypeError, AttributeError):
            raise CutflowHandoffError(
                "CUTFLOW_POLICY_INVALID", "自动剪辑尚未绑定有效的 OOTD 母版"
            ) from None
        assets = list(await session.scalars(select(Asset).where(
            Asset.parent_task_id == task_id, Asset.role == "PRODUCT", Asset.deleted_at.is_(None),
        )))
        try:
            references = snapshot_references(assets, task_id)
        except ValueError:
            raise CutflowHandoffError(
                "CUTFLOW_PRODUCT_REFERENCE_REQUIRED", "自动剪辑需要原任务的衣服商品参考图"
            ) from None
        results = await session.scalars(select(ValidationResult).where(
            ValidationResult.attempt_id == attempt.id,
        ).order_by(ValidationResult.created_at, ValidationResult.id))
        automatic_checks = [{
            "status": result.status.value, "coverage": result.coverage,
            "rule_version": result.rule_version, "model_version": result.model_version,
            "issue_codes": result.issue_codes_json,
            "created_at": result.created_at.isoformat(),
            "source_artifact_id": str(artifact.id), "source_sha256": artifact.sha256,
        } for result in results]
    handoff_id = uuid.uuid4()
    handoff = CutflowHandoff(
        id=handoff_id,
        user_id=user_id,
        parent_task_id=task_id,
        variant_id=variant_id,
        artifact_id=artifact.id,
        handoff_key=f"handoff-{handoff_id}",
        status="QUEUED",
        contract_version=(LAN_CONTRACT if settings.cutflow_transport == "lan"
                          else AUTO_CONTRACT if policy else CONTRACT),
        manifest_path="",
        manifest_json={
            **({"edit_policy": policy} if policy else {}),
            **({"product_references": references} if references else {}),
            **({"automatic_checks": automatic_checks} if policy else {}),
            "source": {
                "user_id": str(user_id),
                **site,
                "site_profile_id": site_profile_id,
                "site_profile_version": task.site_profile_version,
                "task_id": str(task_id),
                "variant_id": str(variant_id),
                "attempt_id": str(attempt.id),
                "artifact_id": str(artifact.id),
                "title": task.title,
                "video_type": task_video_type(task),
                "videoType": task_video_type(task),
                **({
                    "product_id": product_id,
                    "product_binding_status": "recorded" if product_id else "not_recorded",
                    "shot_id": context.get("shotId"),
                    "generation_status": variant.generation_status.value,
                    "download_status": artifact.status.value,
                    "technical_validation_status": variant.validation_status.value,
                    "source_approval_required": False,
                } if policy else {}),
            },
        },
    )
    try:
        async with session.begin_nested():
            session.add(handoff)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(existing_query)
        if existing is None:
            raise
        handoff_identity(existing)
        return existing
    return handoff


async def publish_package(
    handoff: CutflowHandoff,
    artifact: DownloadArtifact,
    settings: Settings,
    object_store: ObjectStore,
    *,
    product_references: list[Asset] | None = None,
) -> dict[str, Any]:
    identity = handoff_identity(handoff)
    policy = saved_policy(handoff)
    references = []
    if policy:
        try:
            references = validate_reference_snapshot(handoff, product_references or [])
        except ValueError:
            raise CutflowHandoffError(
                "CUTFLOW_PRODUCT_REFERENCE_INVALID", "商品参考图已变化、失效或缺失，请重新核对"
            ) from None
    root = Path(settings.cutflow_handoff_root)
    package = safe_path(root, f"inbox/{handoff.handoff_key}")
    local = settings.artifact_root / "cutflow-cache" / f"{handoff.id}.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = await object_store.localize(artifact.object_key, local)
        expected = await asyncio.to_thread(sha256_file, source)
        if artifact.sha256 and expected != artifact.sha256:
            raise CutflowHandoffError("VIDEO_HASH_MISMATCH", "视频文件哈希校验失败")
        destination = safe_path(package, "assets/generated-video.mp4")
        size = await asyncio.to_thread(
            copy_verified,
            source,
            destination,
            expected,
            settings.cutflow_max_media_bytes,
        )
        published_references = []
        for reference in references:
            extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[
                reference["mime_type"]
            ]
            relative = f"product-references/{reference['asset_id']}.{extension}"
            cache = (
                settings.artifact_root / "cutflow-cache"
                / f"{handoff.id}-{reference['asset_id']}"
            )
            try:
                image = await object_store.localize(reference["object_key"], cache)
                copied_size = await asyncio.to_thread(
                    copy_verified, image, safe_path(package, relative), reference["sha256"],
                    settings.cutflow_max_media_bytes,
                )
                if copied_size != reference["size_bytes"]:
                    raise CutflowHandoffError(
                        "CUTFLOW_PRODUCT_REFERENCE_INVALID", "商品参考图大小与登记记录不一致"
                    )
                published_references.append({
                    **{key: value for key, value in reference.items() if key != "object_key"},
                    "relative_path": relative, "role": "product_reference",
                    "applies_to_asset_id": str(handoff.variant_id),
                    "user_id": str(handoff.user_id),
                    "site_id": handoff.manifest_json["source"].get("site_id"),
                    "product_id": handoff.manifest_json["source"].get("product_id"),
                })
            finally:
                await asyncio.to_thread(cache.unlink, missing_ok=True)
        manifest = {
            "schema_version": handoff.contract_version,
            **({"edit_policy": policy} if policy else {}),
            **({"product_references": published_references} if references else {}),
            **({"automatic_checks": handoff.manifest_json.get("automatic_checks", [])}
               if policy else {}),
            "handoff_id": handoff.handoff_key,
            "source": handoff.manifest_json["source"],
            "assets": [
                {
                    "asset_id": str(handoff.variant_id),
                    "relative_path": "assets/generated-video.mp4",
                    "sha256": expected,
                    "size_bytes": size,
                    "duration_seconds": artifact.duration_seconds,
                    "width": artifact.width,
                    "height": artifact.height,
                    "order": 1,
                    "role": "generated_video",
                }
            ],
            "edit_intent": {
                "mode": "guided_auto",
                "video_type": handoff.manifest_json["source"].get("video_type", "ootd"),
                "remove_visual_failures": True,
                "remove_slow_or_redundant_shots": True,
                "remove_unclear_product_shots": True,
                "allow_multi_segment_assembly": True,
                "preserve_source_order": True,
                "preserve_source_audio": False,
                "bgm": "auto_match",
                "beat_sync": True,
            } if handoff.contract_version == LAN_CONTRACT
            and policy["version"] == POLICY_VERSION else {
                "preserve_source_audio": artifact.has_audio,
            },
            "created_at": handoff.created_at.isoformat(),
        }
        digest = await asyncio.to_thread(write_json_atomic, package / "manifest.json", manifest)
        await asyncio.to_thread(write_json_atomic, package / "READY", {"sha256": digest})
        return {
            "handoffId": handoff.handoff_key,
            **identity,
            **({"contractVersion": LAN_CONTRACT,
                "manifestRelativePath": f"inbox/{handoff.handoff_key}/manifest.json"}
               if handoff.contract_version == LAN_CONTRACT else {}),
            "manifestSha256": digest,
            **({"editPolicy": policy} if policy else {}),
        }
    finally:
        # LocalObjectStore returns its original object; only remove our own cache.
        await asyncio.to_thread(local.unlink, missing_ok=True)


async def import_outputs(
    handoff: CutflowHandoff,
    outputs: list[dict[str, Any]],
    settings: Settings,
    object_store: ObjectStore,
) -> list[dict[str, Any]]:
    from libtv_agent.services.media import technical_validate

    if len(outputs) > 50:
        raise CutflowHandoffError("INVALID_RESULT", "回传成片数量超过上限")
    saved = list(handoff.outputs_json or [])
    known = {item["id"]: item for item in saved}
    root = safe_path(Path(settings.cutflow_handoff_root), f"outbox/{handoff.handoff_key}")
    for output in outputs:
        output_id = output.get("id", "")
        digest = output.get("sha256", "")
        if (
            not isinstance(output_id, str)
            or not SAFE_SEGMENT.fullmatch(output_id)
            or (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest))
        ):
            raise CutflowHandoffError("INVALID_RESULT", "回传版本标识或哈希无效")
        if output_id in known:
            if known[output_id]["sha256"] != digest:
                raise CutflowHandoffError("IMMUTABLE_RESULT_CHANGED", "回传版本内容发生变化")
            continue
        source = safe_path(root, output["relative_path"])
        cache = settings.artifact_root / "cutflow-cache" / f"{handoff.id}-{output_id}.mp4"
        try:
            size = await asyncio.to_thread(
                copy_verified,
                source,
                cache,
                digest,
                settings.cutflow_max_media_bytes,
            )
            if size != output.get("size_bytes"):
                raise CutflowHandoffError("INVALID_RESULT", "回传文件大小不匹配")
            await asyncio.to_thread(_decode_check, cache)
            metadata = await asyncio.wait_for(
                technical_validate(
                    cache,
                    duration_min=0.1,
                    duration_max=300,
                    target_ratio=1,
                    ratio_tolerance=100,
                    allow_audio=True,
                ),
                timeout=120,
            )
            if not metadata.passed or metadata.width <= 0 or metadata.height <= 0:
                raise CutflowHandoffError("INVALID_RESULT", "回传视频技术校验失败")
            key = await object_store.put_file(
                cache,
                f"cutflow/{handoff.user_id}/{handoff.id}/{output_id}.mp4",
                "video/mp4",
            )
            saved.append(
                {
                    "id": output_id,
                    "sha256": digest,
                    "object_key": key,
                    "size_bytes": size,
                    "duration_seconds": metadata.duration_seconds,
                    "width": metadata.width,
                    "height": metadata.height,
                    "review_status": "PENDING",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            known[output_id] = saved[-1]
        finally:
            await asyncio.to_thread(cache.unlink, missing_ok=True)
    return saved


def _decode_check(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CutflowHandoffError("FFMPEG_REQUIRED", "回传视频校验需要 FFmpeg")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-threads",
                "1",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            check=True,
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise CutflowHandoffError("INVALID_RESULT", "回传视频完整解码失败") from exc
