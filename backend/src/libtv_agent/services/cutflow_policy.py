"""Immutable, opt-in automatic editing policy shared with the CUT Bridge.

The 1.2 contract deliberately fails on older Bridges instead of silently
turning an automatic request into a second manual confirmation screen.
"""

import uuid
from typing import Any

AUTO_CONTRACT = "libtv-to-cutflow/1.2"
LAN_CONTRACT = "libtv-cut-lan/1"
POLICY_VERSION = "yiying-ootd/2"
LEGACY_POLICY_VERSION = "yiying-ootd/1"


def legacy_lan_policy() -> dict[str, Any]:
    """Keep existing LAN snapshots valid without upgrading their edit instructions."""
    return {
        "version": LEGACY_POLICY_VERSION,
        "origin": "yiying-studio",
        "mode": "automatic",
        "templateFamily": "ootd",
        "templateSelection": "cut_local",
        "grouping": "single_source",
        "confirmation": "source_policy",
        "returnOutputs": True,
        "garmentCheck": "exclude_mismatch_review_uncertain",
        "sourceApproval": "not_required",
    }


def lan_policy() -> dict[str, Any]:
    """CUT performs guided automatic assembly while keeping quality decisions local."""
    return {
        "version": POLICY_VERSION,
        "origin": "yiying-studio",
        "mode": "automatic",
        "strategy": "guided_auto",
        "templateFamily": "ootd",
        "templateSelection": "cut_local_optional",
        "grouping": "multi_segment_assembly",
        "confirmation": "source_policy",
        "returnOutputs": True,
        "garmentCheck": "exclude_mismatch_review_uncertain",
        "selectionPolicy": {
            "removeVisualFailures": True,
            "removeSlowOrRedundantShots": True,
            "removeUnclearProductShots": True,
            "preferProductVisibility": True,
            "preferNaturalMotion": True,
            "allowCrossAssetAssembly": True,
            "preserveSourceOrder": True,
        },
        "assemblyPolicy": {
            "structure": "auto_product_edit",
            "avoidEmptyGaps": True,
            "transition": "hard_cut",
        },
        "audioPolicy": {
            "sourceAudio": "mute",
            "bgm": "auto_match",
            "beatSync": True,
        },
        "sourceApproval": "not_required",
    }


def automatic_policy(template_id: str, user_id: uuid.UUID) -> dict[str, Any]:
    template = uuid.UUID(template_id)
    if template.int == 0 or str(template) != template_id:
        raise ValueError("invalid OOTD template UUID")
    return {
        "version": LEGACY_POLICY_VERSION,
        "origin": "yiying-studio",
        "mode": "automatic",
        "templateId": str(template),
        "templateFamily": "ootd",
        "grouping": "single_source",
        "confirmation": "source_policy",
        "requestedBy": str(user_id),
        "returnOutputs": True,
        "garmentCheck": "exclude_mismatch_review_uncertain",
        "sourceApproval": "not_required",
    }


def saved_policy(handoff) -> dict[str, Any] | None:
    payload = handoff.manifest_json
    policy = payload.get("editPolicy" if handoff.manifest_path else "edit_policy")
    if handoff.contract_version == LAN_CONTRACT:
        if policy not in (lan_policy(), legacy_lan_policy()):
            raise ValueError("invalid LAN OOTD policy")
        return policy
    if handoff.contract_version != AUTO_CONTRACT:
        if policy is not None:
            raise ValueError("automatic editing requires the 1.2 contract")
        return None
    if not isinstance(policy, dict):
        raise ValueError("missing automatic editing policy")
    expected = automatic_policy(policy.get("templateId", ""), handoff.user_id)
    if policy != expected:
        raise ValueError("automatic editing policy does not match its owner or supported policy")
    return expected


def acknowledged_policy(response: dict, policy: dict, manifest_sha256: str) -> bool:
    """Only a persisted, matching CUT acknowledgement proves policy adoption."""
    receipt = response.get("editPolicyReceipt")
    if policy.get("version") == POLICY_VERSION:
        return isinstance(receipt, dict) and (
            receipt.get("state") == "accepted"
            and receipt.get("policyVersion") == POLICY_VERSION
            and receipt.get("strategy") == policy.get("strategy") == "guided_auto"
            and receipt.get("manifestSha256") == manifest_sha256
        )
    return isinstance(receipt, dict) and (
        receipt.get("state") == "accepted"
        and receipt.get("policy") == policy
        and receipt.get("manifestSha256") == manifest_sha256
    )


def editing_failure(response: dict) -> tuple[str, str]:
    """Translate known CUT failures without exposing raw remote paths or logs."""
    error = response.get("error")
    code = error.get("code") if isinstance(error, dict) else response.get("errorCode")
    messages = {
        "NO_MATCHING_HOOK": "素材缺少符合 OOTD 要求的开场镜头，请补充素材",
        "SOURCE_INSUFFICIENT": "现有素材不足以完成 OOTD 剪辑，请补充素材",
        "TEMPLATE_NOT_READY": "OOTD 母版暂不可用，请联系管理员检查",
        "QUALITY_REJECTED": "素材未通过剪辑质量检查，请更换素材",
    }
    if isinstance(code, str) and code in messages:
        return f"CUTFLOW_{code}", messages[code]
    return "CUTFLOW_EDIT_FAILED", "自动剪辑失败，原素材已保留，请联系管理员检查"
