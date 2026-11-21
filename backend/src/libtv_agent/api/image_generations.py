from __future__ import annotations

import asyncio
import hashlib
import io
import re
import tempfile
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from libtv_agent.api.dependencies import (
    Actor,
    Session,
    can_review_across_owners,
    require_review_write,
)
from libtv_agent.domain.enums import QualityDecisionStatus
from libtv_agent.domain.models import (
    AuditEvent,
    ImageGenerationBatch,
    ImageGenerationItem,
    ImageGenerationOutput,
)
from libtv_agent.services.account_access import review_users
from libtv_agent.services.brand_identity import append_brand_reference
from libtv_agent.services.cutflow_site import CutflowSiteError, creation_snapshot
from libtv_agent.services.image_generations import (
    ImageBatchConflictError,
    ImageItemSpec,
    create_batch_record,
    find_idempotent_batch,
    owned_batch,
    owned_output,
    request_fingerprint,
    submit_batch,
)
from libtv_agent.services.image_quota import (
    lock_image_quota_user,
    reserve_image_quota,
)
from libtv_agent.services.runtime_control import (
    RuntimeControlBlockedError,
    get_runtime_config,
    reserve_internal_pilot_budget,
)
from libtv_agent.templates.garment_materials import (
    GarmentMaterial,
    garment_material_guidance,
    infer_garment_material,
)
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

router = APIRouter(tags=["image-generations"])

REFERENCE_ROLE_ORDER = {
    "model.reference": 0,
    "model.front": 0,
    "model.back": 0,
    "model.side": 0,
    "product.reference": 1,
    "product.front": 1,
    "product.back": 1,
    "product.side": 1,
}
LEGACY_REFERENCE_ROLES = {
    "model.front",
    "product.front",
    "product.back",
    "model.back",
    "product.side",
    "model.side",
}

FUSION_SOURCE_LOCK = (
    "以模特参考图为人物和场景基准，只替换人物身上的服装。"
    "必须完整保留模特参考中的原始背景与环境，不得抠出人物，不得移除、替换、虚化、扩展或重构背景；"
    "保持人物身份、脸部、发型、身体姿态、手部、下装、配饰、构图、机位、透视、光线、"
    "色调和画面尺寸不变。禁止生成纯色、白色、灰色、透明或摄影棚背景。"
    "只允许在替换后的服装覆盖区域做必要修改。"
)

FUSION_INFERRED_BACK_LOCK = (
    "以图一的人物和原始场景为底图基准，只为呈现背面而自然调整人物朝向并替换上衣。"
    "必须保留图一背景环境的内容、空间关系、构图、机位、透视、光线、色调和画面尺寸，"
    "不得抠出人物，不得移除、替换、虚化、扩展或重构背景；保持人物身份、发型、下装和配饰一致。"
    "禁止生成纯色、白色、灰色、透明或摄影棚背景。"
)
LEGACY_FUSION_SOURCE_LOCK = (
    "以图一为唯一底图进行局部换装编辑，只替换人物身上的上衣为图二服装。"
    "必须完整保留图一原始背景与环境，不得抠出人物，不得移除、替换、虚化、扩展或重构背景；"
    "保持图一人物身份、脸部、发型、身体姿态、手部、下装、配饰、构图、机位、透视、光线、"
    "色调和画面尺寸不变。禁止生成纯色、白色、灰色、透明或摄影棚背景。"
    "只允许在服装覆盖区域做必要修改。"
)
LEGACY_INFERRED_BACK_LOCK = (
    "以图一的人物和原始场景为底图基准，只为呈现背面而自然调整人物朝向并替换上衣。"
    "必须保留图一背景环境的内容、空间关系、构图、机位、透视、光线、色调和画面尺寸，"
    "不得抠出人物，不得移除、替换、虚化、扩展或重构背景；保持人物身份、发型、下装和配饰一致。"
    "禁止生成纯色、白色、灰色、透明或摄影棚背景。"
)


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageQualityDecisionRequest(ManifestModel):
    decision: Literal["QUALIFIED", "REJECTED"]
    product_fidelity_pass: bool = Field(alias="productFidelityPass")
    human_critical_pass: bool = Field(alias="humanCriticalPass")
    human_review_seconds: float = Field(alias="humanReviewSeconds", gt=0, le=28_800)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_decision(self) -> ImageQualityDecisionRequest:
        gates_pass = self.product_fidelity_pass and self.human_critical_pass
        if (self.decision == "QUALIFIED") != gates_pass:
            raise ValueError("QUALIFIED requires every human Required Hard Gate to pass")
        self.notes = " ".join((self.notes or "").split()).strip() or None
        if self.decision == "REJECTED" and not self.notes:
            raise ValueError("REJECTED requires a review reason")
        return self


class ReferenceRole(ManifestModel):
    fileIndex: int = Field(ge=0)
    role: Literal[
        "model.reference",
        "product.reference",
        "model.front",
        "product.front",
        "product.back",
        "model.back",
        "product.side",
        "model.side",
    ]


class StoryboardShot(ManifestModel):
    order: int = Field(ge=1, le=7)
    instruction: str = Field(min_length=1, max_length=1000)


class ImageBatchPrompts(ManifestModel):
    fusion: str = Field(default="", max_length=2800)
    storyboard: str = Field(default="", max_length=2800)


class ImageBatchManifestItem(ManifestModel):
    clientTaskId: str = Field(min_length=1, max_length=160)
    outfitId: str = Field(min_length=1, max_length=160)
    outfitName: str = Field(min_length=1, max_length=160)
    sku: str = Field(default="", max_length=160)
    prompts: ImageBatchPrompts
    userRequirement: str = Field(default="", max_length=1000)
    referenceFileIndexes: list[Annotated[int, Field(ge=0)]] = Field(
        default_factory=list, max_length=6
    )
    modelReferenceFileIndexes: list[Annotated[int, Field(ge=0)]] = Field(
        default_factory=list, max_length=3
    )
    productReferenceFileIndexes: list[Annotated[int, Field(ge=0)]] = Field(
        default_factory=list, max_length=3
    )
    referenceRoles: list[ReferenceRole] = Field(default_factory=list, max_length=6)
    storyboardShots: list[StoryboardShot] = Field(default_factory=list, max_length=7)
    generateBack: bool | None = None

    @model_validator(mode="after")
    def validate_references(self) -> ImageBatchManifestItem:
        if len(set(self.referenceFileIndexes)) != len(self.referenceFileIndexes):
            raise ValueError("referenceFileIndexes must not contain duplicates")
        role_indexes = [reference.fileIndex for reference in self.referenceRoles]
        if role_indexes and role_indexes != self.referenceFileIndexes:
            raise ValueError("referenceRoles must preserve the exact referenceFileIndexes order")
        roles = [reference.role for reference in self.referenceRoles]
        if any(role in LEGACY_REFERENCE_ROLES for role in roles):
            legacy_order = [
                "model.front",
                "product.front",
                "product.back",
                "model.back",
                "product.side",
                "model.side",
            ]
            if roles != sorted(roles, key=legacy_order.index):
                raise ValueError("referenceRoles must preserve the fixed prompt image order")
        elif roles != sorted(roles, key=REFERENCE_ROLE_ORDER.__getitem__):
            raise ValueError(
                "referenceRoles must preserve model references before product references"
            )
        grouped_indexes = self.modelReferenceFileIndexes + self.productReferenceFileIndexes
        if grouped_indexes and grouped_indexes != self.referenceFileIndexes:
            raise ValueError("reference groups must preserve the exact referenceFileIndexes order")
        if grouped_indexes and any(
            reference.fileIndex not in grouped_indexes for reference in self.referenceRoles
        ):
            raise ValueError("every reference role must belong to a reference group")
        shot_orders = [shot.order for shot in self.storyboardShots]
        if shot_orders and shot_orders != list(range(1, len(shot_orders) + 1)):
            raise ValueError("storyboard shot order must be contiguous and start at 1")
        return self


def _compile_prompt(
    base_prompt: str,
    *,
    task_instruction: str,
    user_requirement: str,
    garment_material: GarmentMaterial,
) -> str:
    base = base_prompt.strip()
    task = task_instruction.strip()
    requirement = user_requirement.strip()
    material = garment_material_guidance(garment_material)
    material_section = f"商品材质与结构要求：\n{material}"
    if not requirement:
        return "\n\n".join(part for part in (base, material_section, task) if part)
    sections = [f"基础生成要求：\n{base}", material_section]
    if task:
        sections.append(f"当前任务要求：\n{task}")
    sections.extend(
        (
            f"用户补充要求（优先）：\n{requirement}",
            "冲突处理规则：用户补充要求与基础生成要求或当前任务要求冲突时，"
            "以用户补充要求为准；未被用户补充要求修改的内容继续遵循原要求。",
        )
    )
    return "\n\n".join(sections)


class ImageBatchManifest(ManifestModel):
    clientBatchId: str = Field(min_length=1, max_length=160)
    siteProfileId: str | None = Field(default=None, min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    provider: Literal["gpt", "nanobanana"]
    modes: list[Literal["fusion", "storyboard"]] = Field(min_length=1, max_length=1)
    resultCount: int = Field(ge=1, le=4)
    aspectRatio: str = Field(default="9:16", pattern=r"^[1-9]\d*:[1-9]\d*$")
    resolution: Literal["1k", "2k", "4k"] = "2k"
    quality: Literal["low", "medium", "high"] | None = None
    outputFormat: Literal["png", "jpeg"] = "png"
    allowBackInference: bool = True
    paidSubmissionConsent: Literal[True]
    expectedGenerationCount: int = Field(ge=1, le=40)
    items: list[ImageBatchManifestItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_provider_options(self) -> ImageBatchManifest:
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("modes must not contain duplicates")
        if self.provider == "nanobanana" and self.quality is not None:
            raise ValueError("quality is not supported by Nano Banana 2.1")
        if self.provider == "gpt" and self.quality is None:
            self.quality = "medium"
        return self


def _storyboard_shots(item: ImageBatchManifestItem) -> list[StoryboardShot]:
    if item.storyboardShots:
        return item.storyboardShots
    normalized = re.sub(r"\s+", " ", item.prompts.storyboard.strip())
    if "生成一张" in normalized:
        return [StoryboardShot(order=1, instruction=normalized)]
    parts = [part.strip() for part in re.split(r"[。！？!?；;\n]+", normalized) if part.strip()]
    if len(parts) == 1:
        parts = [
            part.strip()
            for part in re.split(r"(?:，|,|然后|随后|最后|再拍|接着)", normalized)
            if len(part.strip()) >= 2
        ]
    return [
        StoryboardShot(order=index, instruction=text) for index, text in enumerate(parts[:7], 1)
    ]


def _reference_groups(item: ImageBatchManifestItem) -> tuple[list[int], list[int]]:
    model_indexes = list(item.modelReferenceFileIndexes)
    product_indexes = list(item.productReferenceFileIndexes)
    if not model_indexes and not product_indexes and item.referenceRoles:
        model_indexes = [
            reference.fileIndex
            for reference in item.referenceRoles
            if reference.role.startswith("model.")
        ]
        product_indexes = [
            reference.fileIndex
            for reference in item.referenceRoles
            if reference.role.startswith("product.")
        ]
    if not model_indexes or not product_indexes:
        raise ValueError(
            f"{item.clientTaskId} requires at least one model and one product reference"
        )
    if model_indexes + product_indexes != item.referenceFileIndexes:
        raise ValueError(f"{item.clientTaskId} reference groups must match referenceFileIndexes")
    return model_indexes, product_indexes


def _reference_range(indexes: list[int]) -> str:
    if len(indexes) == 1:
        return f"图{indexes[0] + 1}"
    return f"图{indexes[0] + 1}至图{indexes[-1] + 1}"


def _legacy_reference_map(item: ImageBatchManifestItem) -> dict[str, int]:
    return {reference.role: reference.fileIndex for reference in item.referenceRoles}


def _legacy_generate_back(manifest: ImageBatchManifest, item: ImageBatchManifestItem) -> bool:
    if item.generateBack is not None:
        return item.generateBack
    roles = {reference.role for reference in item.referenceRoles}
    return (
        ("model.back" in roles and "product.back" in roles)
        or (manifest.allowBackInference and ("model.back" in roles or "product.back" in roles))
    )


def _expand_manifest(manifest: ImageBatchManifest, file_count: int) -> list[ImageItemSpec]:
    specs: list[ImageItemSpec] = []
    # Classic has six fixed reference roles; both current models support all six.
    max_references = 6
    for item in manifest.items:
        if any(index >= file_count for index in item.referenceFileIndexes):
            raise ValueError(f"{item.clientTaskId} references a file index that was not uploaded")
        if len(item.referenceFileIndexes) > max_references:
            raise ValueError(
                f"{manifest.provider} accepts at most {max_references} references per prediction"
            )
        is_legacy = any(
            reference.role in LEGACY_REFERENCE_ROLES for reference in item.referenceRoles
        )
        if is_legacy:
            references = _legacy_reference_map(item)
            model_indexes = [
                index for role, index in references.items() if role.startswith("model.")
            ]
            product_indexes = [
                index for role, index in references.items() if role.startswith("product.")
            ]
        else:
            references = {}
            model_indexes, product_indexes = _reference_groups(item)
        if "fusion" in manifest.modes:
            if not item.prompts.fusion.strip():
                raise ValueError(f"{item.clientTaskId} requires a fusion prompt")
            views = [
                (
                    "front",
                    "正面融合图" if is_legacy else "换装图",
                    (references["model.front"], references["product.front"])
                    if is_legacy
                    else tuple(item.referenceFileIndexes),
                    (
                        f"当前生成正面融合图：图一为人物正面，图二为服装正面。{LEGACY_FUSION_SOURCE_LOCK}"
                        if is_legacy
                        else (
                            f"当前生成换装图：{_reference_range(model_indexes)}为模特参考，"
                            f"{_reference_range(product_indexes)}为服装参考。{FUSION_SOURCE_LOCK}"
                        )
                    ),
                )
            ]
            if is_legacy and _legacy_generate_back(manifest, item):
                model_role = "model.back" if "model.back" in references else "model.front"
                product_role = "product.back" if "product.back" in references else "product.front"
                inferred_roles = [
                    label
                    for role, label in (("model.back", "人物背面"), ("product.back", "服装背面"))
                    if role not in references
                ]
                inference_note = (
                    f"缺少{'、'.join(inferred_roles)}，请依据现有参考自然推测背面。"
                    if inferred_roles
                    else ""
                )
                view_lock = (
                    LEGACY_INFERRED_BACK_LOCK
                    if model_role == "model.front"
                    else LEGACY_FUSION_SOURCE_LOCK
                )
                views.append((
                    "back",
                    "背面融合图",
                    (references[model_role], references[product_role]),
                    f"当前生成背面融合图：图一为"
                    f"{('人物背面' if model_role == 'model.back' else '人物正面')}参考，"
                    f"图二为{('服装背面' if product_role == 'product.back' else '服装正面')}参考。"
                    f"{inference_note}{view_lock}",
                ))
            for view, label, view_references, view_instruction in views:
                garment_material = infer_garment_material(
                    item.outfitName,
                    item.prompts.fusion,
                    item.userRequirement,
                )
                prompt = _compile_prompt(
                    item.prompts.fusion,
                    task_instruction=view_instruction,
                    user_requirement=item.userRequirement,
                    garment_material=garment_material,
                )
                for _ in range(manifest.resultCount):
                    specs.append(
                        ImageItemSpec(
                            item.clientTaskId,
                            item.outfitId,
                            item.outfitName,
                            item.sku,
                            "fusion",
                            view,
                            label,
                            prompt,
                            view_references,
                        )
                    )
        if "storyboard" in manifest.modes:
            if is_legacy and "图三" in item.prompts.storyboard and "product.back" not in references:
                raise ValueError(
                    f"{item.clientTaskId} storyboard prompt references 图三 "
                    "but has no product back image"
                )
            storyboard_references = (
                tuple(
                    references[role]
                    for role in ("model.front", "product.front", "product.back")
                    if role in references
                )
                if is_legacy
                else tuple(item.referenceFileIndexes)
            )
            shots = _storyboard_shots(item)
            if not shots:
                raise ValueError(f"{item.clientTaskId} requires at least one storyboard shot")
            for shot in shots:
                garment_material = infer_garment_material(
                    item.outfitName,
                    item.prompts.storyboard,
                    item.userRequirement,
                )
                prompt = _compile_prompt(
                    item.prompts.storyboard,
                    task_instruction=f"Shot {shot.order}: {shot.instruction}",
                    user_requirement=item.userRequirement,
                    garment_material=garment_material,
                )
                for _ in range(manifest.resultCount):
                    specs.append(
                        ImageItemSpec(
                            item.clientTaskId,
                            item.outfitId,
                            item.outfitName,
                            item.sku,
                            "storyboard",
                            "shot",
                            f"分镜 {shot.order}",
                            prompt,
                            storyboard_references,
                        )
                    )
    if len(specs) != manifest.expectedGenerationCount:
        raise ValueError(
            "expectedGenerationCount does not match the server expansion "
            f"({manifest.expectedGenerationCount} != {len(specs)})"
        )
    return specs


def _public_status(value: str) -> str:
    return {
        "CREATED": "queued",
        "SUBMITTING": "queued",
        "PENDING": "queued",
        "PROCESSING": "running",
        "COMPLETED": "completed",
        "PARTIAL_FAILED": "failed",
        "FAILED": "failed",
        "CANCELLED": "failed",
        "TIMEOUT": "failed",
        "WRITE_UNCERTAIN": "write_uncertain",
        "MANUAL_REVIEW": "manual_review",
    }.get(value, value.lower())


def _public_error(error: Any) -> dict[str, Any] | None:
    if not error:
        return None
    raw_code = error.get("code", "IMAGE_GENERATION_FAILED")
    code = str(raw_code)
    if len(code) > 80 or not all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in code):
        code = "IMAGE_GENERATION_FAILED"
    details = error.get("details") or {}
    return {
        "code": code,
        "message": "Image processing could not be completed; review the error code and task state",
        "retryable": error.get("retryable") is True,
        "details": {"submitted": details["submitted"]}
        if isinstance(details, dict) and type(details.get("submitted")) is bool
        else {},
    }


def _reference_urls(batch: Any, requested: dict[str, Any]) -> dict[str, list[str]]:
    """Expose only owned, persisted source references; never expose object-store keys."""
    stored = {
        asset["index"]
        for asset in (batch.request_json or {}).get("referenceAssets", [])
        if isinstance(asset, dict) and type(asset.get("index")) is int
    }
    refs: dict[str, list[str]] = {"model": [], "product": []}
    roles = requested.get("referenceRoles") or [
        *({"fileIndex": index, "role": "model.reference"}
          for index in requested.get("modelReferenceFileIndexes", [])),
        *({"fileIndex": index, "role": "product.reference"}
          for index in requested.get("productReferenceFileIndexes", [])),
    ]
    for reference in roles:
        index = reference.get("fileIndex")
        role = reference.get("role", "")
        kind = ("model" if role.startswith("model.") else
                "product" if role.startswith("product.") else None)
        if kind and index in stored:
            url = f"/api/v1/image-generation-batches/{batch.id}/references/{index}"
            if url not in refs[kind]:
                refs[kind].append(url)
    return refs


async def _persist_reference_files(
    request: Request, session: Session, batch: ImageGenerationBatch, paths: list[Path]
) -> None:
    """Archive original model/product images before the temporary upload folder disappears."""
    current = dict(batch.request_json or {})
    if len(current.get("referenceAssets", [])) == len(paths):
        return
    content_types = {
        ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp",
    }
    assets = []
    for index, path in enumerate(paths):
        key = f"image-generation-sources/{batch.user_id}/{batch.id}/{index}{path.suffix}"
        saved_key = await request.app.state.object_store.put_file(
            path, key, content_types[path.suffix.lower()]
        )
        assets.append({
            "index": index, "objectKey": saved_key,
            "contentType": content_types[path.suffix.lower()],
            "suffix": path.suffix.lower(),
        })
    batch.request_json = {**current, "referenceAssets": assets}
    await session.commit()


def batch_response(batch: Any) -> dict[str, Any]:
    manifest = batch.request_json or {}
    requested_items = {item["clientTaskId"]: item for item in manifest.get("items", [])}
    grouped: dict[str, list[Any]] = {}
    for prediction in batch.items:
        grouped.setdefault(prediction.client_task_id, []).append(prediction)
    items: list[dict[str, Any]] = []
    for predictions in grouped.values():
        first = predictions[0]
        requested = requested_items.get(first.client_task_id, {})
        statuses = {prediction.status for prediction in predictions}
        if "MANUAL_REVIEW" in statuses:
            aggregate_status = "MANUAL_REVIEW"
        elif "WRITE_UNCERTAIN" in statuses:
            aggregate_status = "WRITE_UNCERTAIN"
        elif statuses <= {"COMPLETED"}:
            aggregate_status = "COMPLETED"
        elif statuses & {"CREATED", "PROCESSING", "SUBMITTING", "PENDING"}:
            aggregate_status = "PROCESSING"
        else:
            aggregate_status = "FAILED"
        outputs = []
        for prediction in predictions:
            for output in prediction.outputs:
                if not output.object_key:
                    continue
                local_url = f"/api/v1/image-generation-outputs/{output.id}"
                outputs.append(
                    {
                        "id": str(output.id),
                        "kind": prediction.kind,
                        "view": prediction.view,
                        "label": prediction.label,
                        "url": local_url,
                        "downloadUrl": local_url,
                        "artifactId": str(output.id),
                        "qualificationStatus": output.qualification_status.value,
                        "qualityGates": dict(output.quality_gates_json or {}),
                        "reviewedAt": (
                            output.reviewed_at.isoformat() if output.reviewed_at else None
                        ),
                        "humanReviewSeconds": output.human_review_seconds,
                    }
                )
        error = next(
            (prediction.error_json for prediction in predictions if prediction.error_json),
            None,
        )
        provider_ids = [
            prediction.provider_task_id
            for prediction in predictions
            if prediction.provider_task_id is not None
        ]
        items.append(
            {
                "id": str(first.id),
                "clientTaskId": first.client_task_id,
                "outfitId": first.outfit_id,
                "outfitName": first.outfit_name,
                "sku": first.sku,
                "prompts": {
                    key: requested.get("prompts", {}).get(key, "")
                    for key in ("fusion", "storyboard")
                },
                "userRequirement": requested.get("userRequirement", ""),
                "referenceUrls": _reference_urls(batch, requested),
                "storyboardShots": [
                    {"order": shot["order"], "instruction": shot["instruction"]}
                    for shot in requested.get("storyboardShots", [])
                ],
                "status": _public_status(aggregate_status),
                "providerTaskId": provider_ids[0] if len(provider_ids) == 1 else None,
                "providerTaskIds": provider_ids,
                "modelId": first.model_id,
                "outputs": outputs,
                "error": _public_error(error),
            }
        )
    return {
        "id": str(batch.id),
        "clientBatchId": batch.client_batch_id,
        "name": batch.name,
        "provider": batch.provider,
        "configuration": {
            "siteProfileId": manifest.get("siteProfileId"),
            "modes": manifest.get("modes", ["fusion"]),
            "resultCount": manifest.get("resultCount", 1),
            "aspectRatio": manifest.get("aspectRatio", "9:16"),
            "resolution": manifest.get("resolution", "2k"),
            "quality": manifest.get("quality") or "medium",
            "allowBackInference": manifest.get("allowBackInference", True),
        },
        "status": _public_status(batch.status),
        "expectedGenerationCount": batch.expected_generation_count,
        "items": items,
        "createdAt": batch.created_at.isoformat() if batch.created_at else None,
        "updatedAt": (
            batch.__dict__["updated_at"].isoformat() if batch.__dict__.get("updated_at") else None
        ),
    }


async def _read_uploads(
    files: list[UploadFile], *, max_bytes: int, root: Path
) -> tuple[list[Path], list[str]]:
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    paths: list[Path] = []
    hashes: list[str] = []
    for index, upload in enumerate(files):
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in allowed_suffixes:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "IMAGE_TYPE_UNSUPPORTED",
                    "message": f"file {index} is not JPG, PNG, or WebP",
                },
            )
        data = await upload.read(max_bytes + 1)
        if not data:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "image file cannot be empty")
        if len(data) > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "image exceeds configured limit")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                image_format = image.format
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "IMAGE_INVALID", "message": f"file {index} cannot be decoded"},
            ) from exc
        expected_format = {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
        }[suffix]
        if image_format != expected_format:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "IMAGE_TYPE_MISMATCH",
                    "message": f"file {index} extension does not match its image data",
                },
            )
        path = root / f"{index}{suffix}"
        await asyncio.to_thread(path.write_bytes, data)
        paths.append(path)
        hashes.append(hashlib.sha256(data).hexdigest())
    return paths, hashes


@router.post("/image-generation-batches", status_code=status.HTTP_201_CREATED)
async def create_image_generation_batch(
    actor: Actor,
    session: Session,
    request: Request,
    client_manifest: Annotated[str, Form(alias="clientManifest")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, Any]:
    try:
        manifest = ImageBatchManifest.model_validate_json(client_manifest)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "IMAGE_BATCH_INVALID", "message": str(exc)},
        ) from exc
    if not hasattr(request.app.state, "wavespeed_adapter"):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "WAVESPEED_NOT_CONFIGURED", "message": "WaveSpeed is not configured"},
        )
    settings = request.app.state.settings
    if not settings.wavespeed_enabled or not settings.effective_wavespeed_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WAVESPEED_NOT_CONFIGURED",
                "message": "WaveSpeed is disabled or its API key is not configured",
            },
        )
    runtime = await get_runtime_config(session, settings)
    runtime_json = runtime.config_json if runtime is not None else {}
    if runtime_json.get("globalPaused", settings.global_paused):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "GLOBAL_PAUSED", "message": "new generation work is paused"},
        )
    if runtime_json.get("paidSubmissionsPaused", settings.paid_submissions_paused):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PAID_SUBMISSIONS_PAUSED", "message": "paid submissions are paused"},
        )
    availability_snapshot = getattr(
        request.app.state.wavespeed_adapter, "availability_snapshot", None
    )
    if callable(availability_snapshot):
        availability = availability_snapshot()
        if not availability.available:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": availability.status,
                    "message": availability.reason or "WaveSpeed is unavailable",
                },
            )
    manifest_json = manifest.model_dump(mode="json")
    if manifest.siteProfileId is None:
        manifest_json.pop("siteProfileId")
    uploaded_files = list(files or [])
    if len(uploaded_files) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "IMAGE_BATCH_INVALID",
                "message": "an image batch accepts at most 64 uploaded files",
            },
        )
    await asyncio.to_thread(settings.runner_root.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="image-batch-", dir=settings.runner_root
    ) as temporary_directory:
        paths, hashes = await _read_uploads(
            uploaded_files,
            max_bytes=request.app.state.settings.max_input_image_bytes,
            root=Path(temporary_directory),
        )
        referenced_indexes = {
            index for item in manifest.items for index in item.referenceFileIndexes
        }
        if referenced_indexes != set(range(len(paths))):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "IMAGE_BATCH_INVALID",
                    "message": "every uploaded image must be referenced by the manifest",
                },
            )
        fingerprint = request_fingerprint(manifest_json, hashes)
        await lock_image_quota_user(session, user_id=actor.id)
        try:
            existing = await find_idempotent_batch(
                session,
                user_id=actor.id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
            )
        except ImageBatchConflictError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": str(exc)},
            ) from exc
        if existing is not None:
            # Batches created before the quota-evidence migration have no
            # reservation row. Backfill it on the idempotent replay without
            # re-running the quota gate or consuming any new budget.
            await reserve_image_quota(
                session,
                batch=existing,
                now=existing.created_at,
            )
            await session.commit()
            await _persist_reference_files(request, session, existing, paths)
            await submit_batch(
                session,
                batch=existing,
                adapter=request.app.state.wavespeed_adapter,
                file_paths=paths,
            )
            return batch_response(existing)
        try:
            source_site = await creation_snapshot(session, actor.id, manifest.siteProfileId)
        except CutflowSiteError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "CUTFLOW_SITE_REQUIRED", "message": str(exc)},
            ) from exc
        if source_site is not None:
            manifest_json["siteProfileId"] = source_site["profileId"]
            manifest_json["cutflowSite"] = source_site
        try:
            specs = _expand_manifest(manifest, len(paths))
            if source_site:
                specs = [replace(spec, prompt=append_brand_reference(
                    spec.prompt, source_site["brandReference"],
                )) for spec in specs]
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "IMAGE_BATCH_INVALID", "message": str(exc)},
            ) from exc
        # Legacy daily image-count cap is disabled. The USD budget is
        # reserved by reserve_image_quota() alongside the batch write.
        try:
            await reserve_internal_pilot_budget(
                session,
                settings,
                user_id=actor.id,
                units=len(specs),
            )
        except RuntimeControlBlockedError as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": exc.code, "message": str(exc), "retryable": False},
            ) from exc
        batch = create_batch_record(
            user_id=actor.id,
            idempotency_key=idempotency_key,
            request_hash=fingerprint,
            manifest=manifest_json,
            item_specs=specs,
        )
        session.add(batch)
        try:
            await session.flush()
            await reserve_image_quota(session, batch=batch)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await find_idempotent_batch(
                session,
                user_id=actor.id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
            )
            if existing is None:
                raise
            await _persist_reference_files(request, session, existing, paths)
            await submit_batch(
                session,
                batch=existing,
                adapter=request.app.state.wavespeed_adapter,
                file_paths=paths,
            )
            return batch_response(existing)
        await _persist_reference_files(request, session, batch, paths)
        await submit_batch(
            session,
            batch=batch,
            adapter=request.app.state.wavespeed_adapter,
            file_paths=paths,
        )
        return batch_response(batch)


@router.get("/image-generation-batches")
async def list_image_generation_batches(
    actor: Actor,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    # Read persisted history only: opening the list must never submit or poll providers.
    batches = list(
        await session.scalars(
            select(ImageGenerationBatch)
            .where(ImageGenerationBatch.user_id == actor.id)
            .order_by(ImageGenerationBatch.created_at.desc(), ImageGenerationBatch.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return {
        "items": [batch_response(batch) for batch in batches[:limit]],
        "hasMore": len(batches) > limit,
        "nextOffset": offset + min(len(batches), limit),
    }


@router.get("/image-generation-batches/{batch_id}")
async def read_image_generation_batch(
    batch_id: uuid.UUID, actor: Actor, session: Session
) -> dict[str, Any]:
    # Provider polling/materialization belongs to the recovery worker. Browser
    # reads must not wait for its global lock, network calls or image downloads.
    batch = await owned_batch(session, user_id=actor.id, batch_id=batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image generation batch was not found")
    return batch_response(batch)


@router.get("/image-generation-batches/{batch_id}/references/{file_index}")
async def download_image_generation_reference(
    batch_id: uuid.UUID, file_index: int, actor: Actor, session: Session, request: Request
) -> FileResponse:
    batch = await owned_batch(session, user_id=actor.id, batch_id=batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image generation batch was not found")
    asset = next(
        (entry for entry in (batch.request_json or {}).get("referenceAssets", [])
         if type(entry.get("index")) is int and entry["index"] == file_index),
        None,
    )
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reference image was not found")
    try:
        local = await request.app.state.object_store.localize(
            asset["objectKey"],
            request.app.state.settings.artifact_root
            / "cache" / "image-sources" / str(batch.id) / str(file_index),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_410_GONE, "reference image has expired") from exc
    return FileResponse(
        local,
        media_type=asset["contentType"],
        filename=f"source-reference-{file_index}{asset['suffix']}",
    )


@router.get("/image-generation-outputs/{output_id}")
async def download_image_generation_output(
    output_id: uuid.UUID, actor: Actor, session: Session, request: Request
) -> FileResponse:
    output = await owned_output(session, user_id=actor.id, output_id=output_id)
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image output was not found")
    if not output.object_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "image output is not materialized")
    try:
        local = await request.app.state.object_store.localize(
            output.object_key,
            request.app.state.settings.artifact_root / "cache" / "image-outputs" / str(output.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_410_GONE, "image output has expired") from exc
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(output.content_type, ".img")
    return FileResponse(
        local,
        media_type=output.content_type or "application/octet-stream",
        filename=f"generated-{output.id}{extension}",
    )


@router.put("/image-generation-outputs/{output_id}/quality-decision")
async def put_image_quality_decision(
    output_id: uuid.UUID,
    payload: ImageQualityDecisionRequest,
    actor: Actor,
    session: Session,
) -> dict[str, Any]:
    require_review_write(actor)
    if can_review_across_owners(actor):
        output = await session.scalar(
            select(ImageGenerationOutput)
            .join(ImageGenerationItem, ImageGenerationItem.id == ImageGenerationOutput.item_id)
            .join(ImageGenerationBatch, ImageGenerationBatch.id == ImageGenerationItem.batch_id)
            .where(
                ImageGenerationOutput.id == output_id,
                ImageGenerationBatch.user_id.in_(review_users(actor)),
            )
        )
    else:
        output = await owned_output(session, user_id=actor.id, output_id=output_id)
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image output was not found")
    if not output.object_key or not output.sha256:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "IMAGE_TECHNICAL_HARD_GATE_NOT_PASSED",
                "message": "quality decision requires a materialized, checksummed image",
            },
        )
    previous = output.qualification_status
    output.qualification_status = QualityDecisionStatus(payload.decision)
    output.quality_gates_json = {
        "PRODUCT_FIDELITY": {
            "verification": "HUMAN_REQUIRED",
            "outcome": "PASS" if payload.product_fidelity_pass else "FAIL",
        },
        "HUMAN_CRITICAL": {
            "verification": "HUMAN_REQUIRED",
            "outcome": "PASS" if payload.human_critical_pass else "FAIL",
        },
        "TECHNICAL_CRITICAL": {
            "verification": "MACHINE_VERIFIED",
            "outcome": "PASS",
            "sha256": output.sha256,
        },
    }
    output.reviewed_by = actor.id
    output.reviewed_at = datetime.now(UTC)
    output.human_review_seconds = payload.human_review_seconds
    output.review_notes = payload.notes
    session.add(
        AuditEvent(
            actor_id=actor.id,
            action="IMAGE_QUALITY_DECISION",
            entity_type="image_generation_output",
            entity_id=str(output.id),
            from_state=previous.value,
            to_state=output.qualification_status.value,
            reason=payload.notes or "Required Hard Gates reviewed",
            correlation_id=str(uuid.uuid4()),
            evidence_refs_json=[f"image_generation_item:{output.item_id}"],
        )
    )
    await session.commit()
    return {
        "id": str(output.id),
        "qualificationStatus": output.qualification_status.value,
        "qualityGates": output.quality_gates_json,
        "reviewedAt": output.reviewed_at.isoformat(),
        "humanReviewSeconds": output.human_review_seconds,
    }
