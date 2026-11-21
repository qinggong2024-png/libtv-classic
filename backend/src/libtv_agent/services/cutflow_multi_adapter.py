"""Classic-side wire adapter for the isolated CUT multi-source contract.

The adapter is deliberately not enabled by itself. Runtime intake remains behind
the API's separate hard gate until CUT's deployment dependencies are verified.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from libtv_agent.services.brand_identity import brand_identity as resolve_brand
from libtv_agent.services.cutflow_handoff import CutflowHandoffError, safe_path, write_json_atomic
from libtv_agent.services.cutflow_policy import POLICY_VERSION, lan_policy
from libtv_agent.services.cutflow_product_package import native_intake
from libtv_agent.services.cutflow_protocol import (
    JOB_CONTRACT,
    STATUS_SCHEMA,
    check_status_revision,
)
from libtv_agent.services.cutflow_video_type import normalize_video_type

CONTRACT_VERSION = JOB_CONTRACT
IDEMPOTENCY_PREFIX = f"{CONTRACT_VERSION}:"
MULTI_STATUS_MAP = {
    "accepted": "IMPORTING", "processing": "EDITING",
    "queued": "QUEUED", "uploading": "IMPORTING", "importing": "IMPORTING",
    "editing": "EDITING", "review": "PENDING_REVIEW", "completed": "PENDING_REVIEW",
    "failed": "FAILED", "unknown": "UNKNOWN",
}


def multi_handoff_key(task_id: uuid.UUID | str) -> str:
    """Return the stable classic identity for a CUT task UUID."""
    value = str(task_id)
    try:
        return f"multi-{uuid.UUID(value)}"
    except ValueError as exc:
        raise CutflowHandoffError("INVALID_TASK_ID", "多源任务编号无效") from exc


def multi_idempotency_key(task_id: uuid.UUID | str) -> str:
    return IDEMPOTENCY_PREFIX + str(uuid.UUID(str(task_id)))


def _cut_brand_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Separate CUT's brand lookup key from the unchanged business identity."""
    identity = source.get("brandIdentity")
    if not identity:
        return {"brandIdentity": identity}
    reference = source.get("brandReference") or {}
    try:
        uuid.UUID(str(identity))
    except ValueError:
        business_id = source.get("businessBrandId") or reference.get("accountBrandId")
    else:
        business_id = identity
    for candidate in (
        reference.get("profileId"), source.get("siteProfileId"),
        reference.get("accountBrandName"), reference.get("fullName"),
        reference.get("shortName"), identity,
    ):
        brand = resolve_brand(candidate) if isinstance(candidate, str) else None
        if brand is not None:
            # Classic's PPH profile remains magicstation; CUT uses purplehecate.
            normalized = ("purplehecate" if brand.profile_id == "magicstation"
                          else brand.profile_id)
            break
    else:
        if business_id == identity:
            raise CutflowHandoffError(
                "BRAND_IDENTITY_UNAVAILABLE", "原片缺少 CUT 可识别的品牌标识，不能仅传业务 UUID",
            )
        normalized = identity
    return {
        "brandIdentity": normalized,
        **({"businessBrandId": business_id} if business_id else {}),
    }


def build_multi_manifest(task: Any, *, created_at: str | None = None) -> dict[str, Any]:
    """Build the immutable ordered manifest from server-verified DB snapshots."""
    brands = {
        item.get("brandIdentity") for item in task.sources_json
        if item.get("brandIdentity")
    }
    if len(brands) > 1:
        # Defence in depth: snapshot_sources already rejected this, but the
        # manifest is the contract CUT reads, so it must never carry a mixed
        # package even if a future caller skips that check.
        raise CutflowHandoffError("INCOMPATIBLE_BRANDS", "多源剪辑要求所有原片属于同一已核验品牌")
    ref_ids = [
        reference.get("refId")
        for item in task.sources_json for reference in item.get("productReferences") or []
    ]
    if any(not isinstance(value, str) or not value for value in ref_ids) or (
        len(set(ref_ids)) != len(ref_ids)
    ):
        raise CutflowHandoffError(
            "INVALID_PRODUCT_REFERENCE", "商品图引用编号必须按片段唯一，请重新创建剪辑任务",
        )
    sources = []
    wire_brands = set()
    business_brands = set()
    for item in sorted(task.sources_json, key=lambda source: source["order"]):
        brand_fields = _cut_brand_fields(item)
        if brand_fields["brandIdentity"]:
            wire_brands.add(brand_fields["brandIdentity"])
        if brand_fields.get("businessBrandId"):
            business_brands.add(brand_fields["businessBrandId"])
        # Both the documented LAN /1 snake_case names and CUT's multi-source
        # camelCase names are emitted: the classic side is the sender, so it
        # cannot be tolerant at read time, and the receiver must find the key
        # whichever spelling it reads. Order stays authoritative via `order`.
        sources.append({
            "clipId": item["clipId"], "id": item["clipId"], "order": item["order"],
            "relative_path": item["relativePath"],
            "relativePath": item["relativePath"], "sha256": item["sha256"],
            "size_bytes": item["sizeBytes"], "size": item["sizeBytes"],
            "duration_seconds": item["durationSeconds"],
            "durationSeconds": item["durationSeconds"],
            "videoType": normalize_video_type(item.get("videoType")),
            "video_type": normalize_video_type(item.get("videoType")),
            # Identity that the classic side can actually prove, plus the one it
            # cannot. `brandIdentity` is the verified brand snapshot every source
            # already had to match. `productId` has no classic equivalent, so it
            # is emitted as an explicit null instead of being silently absent or
            # faked from the brand: a receiver that requires a product identity
            # must reject this package loudly, not group by brand by accident.
            **brand_fields,
            "productId": item.get("productIdentity"),
            "productReferenceIds": [
                reference.get("refId") for reference in item.get("productReferences") or []
            ],
            "source": {
                "user_id": item["userId"], "task_id": item["taskId"],
                "variant_id": item["variantId"], "artifact_id": item["artifactId"],
                "attempt_id": item["attemptId"],
            },
        })
    if len(wire_brands) > 1 or len(business_brands) > 1:
        raise CutflowHandoffError(
            "INCOMPATIBLE_BRANDS", "多源剪辑要求所有原片属于同一已核验品牌",
        )
    native = native_intake(task.sources_json)
    reference_task_id = (getattr(task, "manifest_json", None) or {}).get("referenceBatchTaskId")
    return {
        **({"nativeIntake": native} if native else {}),
        **({"referenceBatchTaskId": reference_task_id} if reference_task_id else {}),
        "contractVersion": CONTRACT_VERSION, "taskId": str(task.id),
        "idempotencyKey": multi_idempotency_key(task.id),
        "statusSchema": STATUS_SCHEMA,
        "statusRelativePath": f"outbox/{task.id}/status.json",
        "classicReceiptRelativePath": f"receipts/classic/{multi_handoff_key(task.id)}.json",
        # CUT's manifest schema requires the owner and the policy version at the
        # top level; it rejected packages that only carried them as
        # `ownerUserId` in the POST body and `edit_policy.version`.
        "userId": str(task.user_id), "policyVersion": POLICY_VERSION,
        "handoffKey": multi_handoff_key(task.id), "schema_version": CONTRACT_VERSION,
        "handoff_id": multi_handoff_key(task.id), "sources": sources,
        "videoType": (sources[0]["videoType"] if sources and all(
            item["videoType"] == sources[0]["videoType"] for item in sources
        ) else "ootd"),
        # Declared binding granularity. Classic can prove a verified brand, not a
        # product, so the package states that explicitly instead of leaving the
        # receiver to infer it from a null productId.
        # Real product images shipped with the package. CUT's quality gate needs
        # these to verify product consistency; without them it can only degrade
        # to a brand-level check.
        "productReferences": [
            {
                "refId": reference["refId"],
                **({"assetId": reference["assetId"]} if reference.get("assetId") else {}),
                "sourceOrder": item["order"],
                "relativePath": reference["relativePath"],
                "relative_path": reference["relativePath"],
                "sha256": reference["sha256"],
                "size": reference["sizeBytes"],
                "size_bytes": reference["sizeBytes"],
                "mimeType": reference["mimeType"],
                "width": reference["width"],
                "height": reference["height"],
                "filename": reference["filename"],
            }
            for item in sorted(task.sources_json, key=lambda source: source["order"])
            for reference in item.get("productReferences") or []
        ],
        "sourceBinding": (
            {"kind": "brand", "value": next(iter(wire_brands)),
             "granularity": "brand-profile", "productIdProvided": False,
             **({"businessBrandId": next(iter(business_brands))} if business_brands else {})}
            if wire_brands else None
        ),
        "edit_policy": lan_policy(), "created_at": created_at or task.created_at.isoformat(),
    }


def publish_multi_manifest(task: Any, package_root: Path) -> tuple[dict[str, Any], str]:
    """Atomically publish manifest then READY; READY hashes raw manifest bytes."""
    package = safe_path(package_root, f"inbox/multi-{uuid.UUID(str(task.id))}")
    package.mkdir(parents=True, exist_ok=True)
    manifest = build_multi_manifest(task)
    # Write JSON bytes once and hash those exact bytes, avoiding serializer drift.
    raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    manifest_path = safe_path(package, "manifest.json")
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_bytes(raw)
    temporary.replace(manifest_path)
    write_json_atomic(package / "READY", {"sha256": digest})
    return {**manifest, "manifestSha256": digest,
            "manifestRelativePath": f"inbox/multi-{task.id}/manifest.json"}, digest


def cut_multi_request(task: Any, manifest_descriptor: dict[str, Any]) -> dict[str, Any]:
    """Build the versioned request body expected by CUT's multi-source route."""
    manifest = {
        key: value for key, value in manifest_descriptor.items()
        if (
            key not in {"manifestSha256", "manifestRelativePath"}
            and not key.startswith("_")
        )
    }
    if (
        manifest.get("contractVersion") != CONTRACT_VERSION
        or manifest.get("schema_version") != CONTRACT_VERSION
    ):
        raise CutflowHandoffError(
            "CUTFLOW_CONTRACT_UPGRADE_REQUIRED",
            "旧版多源任务不能按新合同重新送剪，请用原素材创建新的 v2 任务",
        )
    return {
        "contractVersion": CONTRACT_VERSION, "taskId": str(task.id),
        "handoffKey": multi_handoff_key(task.id),
        "idempotencyKey": multi_idempotency_key(task.id),
        "notificationOnly": True,
        "statusAuthority": "nas-status",
        "statusSchema": STATUS_SCHEMA,
        "statusRelativePath": manifest_descriptor.get(
            "statusRelativePath", f"outbox/{task.id}/status.json",
        ),
        "classicReceiptRelativePath": manifest_descriptor.get(
            "classicReceiptRelativePath",
            f"receipts/classic/{multi_handoff_key(task.id)}.json",
        ),
        "manifestRelativePath": manifest_descriptor["manifestRelativePath"],
        "manifestSha256": manifest_descriptor["manifestSha256"],
        "manifest": manifest,
        "editPolicy": manifest_descriptor["edit_policy"],
    }


def normalize_cut_multi_response(
    response: dict[str, Any], *, task_id: uuid.UUID | str,
    manifest_sha256: str, policy_version: str = POLICY_VERSION,
    require_revision: bool = False,
) -> dict[str, Any]:
    """Validate CUT identity/proof and project its camelCase result to classic fields."""
    task_id = str(uuid.UUID(str(task_id)))
    expected_handoff_key = multi_handoff_key(task_id)
    receipt = response.get("policyReceipt", response.get("editPolicyReceipt"))
    receipt_handoff = receipt.get("handoffKey") if isinstance(receipt, dict) else None
    if (response.get("taskId") != task_id
            or response.get("handoffKey", receipt_handoff) != expected_handoff_key):
        raise CutflowHandoffError("INVALID_RESULT", "多源任务身份不匹配")
    if response.get("manifestSha256") != manifest_sha256:
        raise CutflowHandoffError("INVALID_RESULT", "多源状态与源文件清单不匹配")
    if not isinstance(receipt, dict) or not (
        receipt.get("state") == "accepted"
        and receipt.get("taskId") == task_id
        and receipt.get("handoffKey") == expected_handoff_key
        and receipt.get("policyVersion") == policy_version
        and receipt.get("manifestSha256") == manifest_sha256
    ):
        raise CutflowHandoffError("CUTFLOW_AUTO_EDIT_NOT_ACCEPTED", "CUT 未确认当前多源剪辑策略")
    remote_status = response.get("status")
    if not isinstance(remote_status, str) or remote_status not in MULTI_STATUS_MAP:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态未识别")
    # CUT may expose an already-rendered file while its V2 completion state is
    # still "processing". Treat it as a non-terminal observation, not a failed
    # delivery or a completed clip. Keep the *raw* snapshot for revision checks.
    # Other non-terminal states still reject unsolicited output manifests.
    premature_processing_outputs = remote_status == "processing" and "outputs" in response
    outputs = [] if premature_processing_outputs else response.get("outputs", [])
    if remote_status not in {"review", "completed", "processing"} and "outputs" in response:
        raise CutflowHandoffError("INVALID_RESULT", "未完成状态不得包含成片字段")
    if remote_status in {"review", "completed"} and "outputs" not in response:
        raise CutflowHandoffError("INVALID_RESULT", "审核或完成状态必须明确回传成片列表")
    if not isinstance(outputs, list) or len(outputs) > 50:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源成片列表无效")
    mapped_outputs = []
    for output in outputs:
        if not isinstance(output, dict):
            raise CutflowHandoffError("INVALID_RESULT", "CUT 多源成片字段无效")
        output_id = output.get("outputId", output.get("id"))
        relative_path = output.get("relativePath", output.get("relative_path"))
        size_bytes = output.get("size", output.get("sizeBytes", output.get("size_bytes")))
        digest = output.get("sha256")
        if (not isinstance(output_id, str) or not output_id
                or not isinstance(relative_path, str) or not relative_path
                or not isinstance(size_bytes, int) or size_bytes < 1
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise CutflowHandoffError("INVALID_RESULT", "CUT 多源成片校验信息缺失")
        mapped_outputs.append({"id": output_id, "relative_path": relative_path,
                               "sha256": digest, "size_bytes": size_bytes})
    if remote_status in {"review", "completed"} and not mapped_outputs:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 报告待审核或完成但没有成片")
    revision = check_status_revision({}, response, required=require_revision)
    return {
        "id": task_id, "handoffKey": expected_handoff_key,
        "cutflowBatchId": response.get("batchId"),
        "status": MULTI_STATUS_MAP[remote_status], "outputs": mapped_outputs,
        "manifestSha256": manifest_sha256,
        "revision": revision.revision,
        "snapshotSha256": revision.fingerprint,
    }
