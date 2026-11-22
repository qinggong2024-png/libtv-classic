"""Optional, best-effort evidence of the exact Kling 3.0 Omni request.

Independent of product direction, retries, billing, quality approval and CUT.
Enable explicitly with ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED=1.  A failed audit
must never fail or retry a paid request. Files remain local/private.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libtv_agent.adapters.kling_api_ootd import (
    _OOTD_PROFILES,
    _IMAGE_TOKEN,
    _CANONICAL_IMAGE,
    _image_alias_conversion_result,
    _native_shots_if_safe,
    _ootd_role_header,
)
from libtv_agent.adapters.video_provider import VideoGenerationRequest

logger = logging.getLogger(__name__)


def audit_enabled() -> bool:
    return os.getenv("ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED", "0").lower() in {
        "1", "true", "yes", "on",
    }


def _hash(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _reference_summary(
    request: VideoGenerationRequest, payload: dict[str, Any],
) -> list[dict[str, Any]]:
    # The provider contents contain base64 images. Never persist base64, paths,
    # provider credentials, callback URL, or the unredacted request payload.
    assets: list[dict[str, Any]] = []
    # Match KlingOfficialApiAdapter._request_image_inputs order exactly.
    remaining_storyboards = len(request.image_paths)
    has_opening = request.first_frame_path is not None or remaining_storyboards > 0
    if request.first_frame_path is None and remaining_storyboards > 0:
        remaining_storyboards -= 1
    intents = (
        (["STORYBOARD_START"] if has_opening else [])
        + (["STORYBOARD_END"] if request.last_frame_path is not None else [])
        + ["STORYBOARD"] * remaining_storyboards
        + ["REFERENCE"] * len(request.refer_image_paths)
        + ["PRODUCT"] * len(request.product_reference_paths)
    )
    image_index = 0
    for item in payload["contents"][1:]:
        if item.get("type") in {"first_frame", "refer_image", "last_frame"}:
            assets.append({
                "role": item["type"], "id": item["id"],
                "intentRole": intents[image_index],
                "sha256": hashlib.sha256(base64.b64decode(item["url"])).hexdigest(),
            })
            image_index += 1
        elif item.get("type") == "element":
            assets.append({"role": "element", "id": item.get("id")})
    return assets


def submission_snapshot(
    request: VideoGenerationRequest, payload: dict[str, Any],
) -> dict[str, Any]:
    """Pure, deterministic evidence derived from the *already built* wire request."""
    wire_prompt = payload["contents"][0]["text"]
    source_prompt = request.prompt
    profile = request.director_native_profile
    model = request.model
    # Reconstruct deterministic formatting stages from the same immutable
    # request used for this paid submission. Do not infer success from an
    # enabled setting or assume that a prompt changed only because of aliases.
    image_roles_enabled = (
        profile in _OOTD_PROFILES
        and (len(request.image_paths) > 1 or len(request.product_reference_paths) > 1)
    )
    role_prompt = _ootd_role_header(request) if image_roles_enabled else source_prompt
    if len(role_prompt) > 3072:
        role_prompt = source_prompt
    image_prompt, image_status, image_changed_count = _image_alias_conversion_result(
        role_prompt, request
    )
    aliases_seen = len(list(_IMAGE_TOKEN.finditer(role_prompt)))
    aliases_seen_in_source = len(list(_IMAGE_TOKEN.finditer(source_prompt)))
    source_image_prompt, source_image_status, source_changed_count = (
        _image_alias_conversion_result(source_prompt, request)
    )
    staged_shots = (
        _native_shots_if_safe(image_prompt, request.duration)
        if profile in _OOTD_PROFILES and request.multi_shot is not False
        else image_prompt
    )
    if profile not in _OOTD_PROFILES:
        conversion_reason = "profile_not_in_native_allowlist"
    elif request.multi_shot is False:
        conversion_reason = "multi_shot_disabled"
    elif staged_shots != image_prompt and wire_prompt == staged_shots:
        conversion_reason = "converted_to_native_shots"
    elif staged_shots != image_prompt:
        conversion_reason = "native_conversion_skipped_after_image_role_binding"
    elif image_prompt.lstrip().startswith("shot 1,"):
        conversion_reason = "already_native_shots_in_source"
    else:
        conversion_reason = "native_shot_pattern_not_losslessly_parseable"
    # Every provider image receives an image_N id. Audit the supplied tokens
    # without reconstructing or persisting any base64 payload.
    actual_image_ids = {
        item["id"]
        for item in payload["contents"][1:]
        if item.get("type") in {"first_frame", "last_frame", "refer_image"}
    }
    wire_ref_ids = set(_CANONICAL_IMAGE.findall(wire_prompt))
    binding_verified = image_status != "unbound_or_ambiguous_reference_fallback" and all(
        token.removeprefix("@") in actual_image_ids for token in wire_ref_ids
    )
    wire_stages_verified = wire_prompt in {image_prompt, staged_shots}
    if not wire_stages_verified:
        image_status = "wire_stage_mismatch"
    return {
        "schema": "allwe-ootd-kling-wire-audit-v1",
        "externalTaskId": request.external_task_id,
        "model": model,
        "directorNativeProfile": profile,
        # Sol's durable original is generation_variant.prompt_text in the DB.
        # The request may have passed through execution stages since then.
        "preAdapterPrompt": source_prompt,
        "preAdapterPromptSha256": _hash(source_prompt),
        "exactKlingPrompt": wire_prompt,
        "exactKlingPromptSha256": _hash(wire_prompt),
        "promptChanged": source_prompt != wire_prompt,
        "nativeShotConversion": conversion_reason,
        "imageReferenceConversion": image_status,
        "imageReferenceSourceConversion": source_image_status,
        "imageReferenceAliasesDetected": aliases_seen,
        "imageReferenceSourceAliasesDetected": aliases_seen_in_source,
        "imageReferenceAliasesChanged": image_changed_count,
        "imageReferenceSourceAliasesChanged": source_changed_count,
        "imageReferenceSourceChanged": source_image_prompt != source_prompt,
        "imageReferenceBindingVerified": binding_verified,
        "imageReferenceWireStageVerified": wire_stages_verified,
        "roleHeaderAdded": role_prompt != source_prompt,
        "settings": {key: payload["settings"].get(key)
                     for key in ("duration", "resolution", "aspect_ratio", "audio", "multi_shot")},
        "imageRoles": _reference_summary(request, payload),
        "providerTaskId": None,
        "submissionState": "WIRE_PREPARED",
    }


def write_snapshot(root: Path, data: dict[str, Any]) -> None:
    """Atomically replace only this external ID's record, never the database."""
    external_id = data.get("externalTaskId")
    if not isinstance(external_id, str) or not external_id:
        return
    base = root / "ootd-wire-audit"
    base.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(external_id.encode("utf-8")).hexdigest()
    target = base / f"{key}.json"
    tmp = base / f"{key}.{os.getpid()}.tmp"
    record = {**data, "recordedAtUtc": datetime.now(UTC).isoformat()}
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def audit_without_blocking(root: Path, data: dict[str, Any]) -> None:
    if not audit_enabled():
        return
    try:
        write_snapshot(root, data)
    except Exception as exc:
        # Never log prompt text or credentials, and never trigger a paid retry.
        logger.warning("OOTD audit write skipped: %s", type(exc).__name__)
