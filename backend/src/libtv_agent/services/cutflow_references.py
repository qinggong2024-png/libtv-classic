"""Task-bound independent garment references, never generated-video frames."""

import re
import uuid

from libtv_agent.domain.models import Asset


def snapshot_references(assets: list[Asset], task_id: uuid.UUID) -> list[dict]:
    selected = [asset for asset in assets if asset.role == "PRODUCT"]
    if not selected or len(selected) > 5:
        raise ValueError("one to five garment references required")
    snapshots = []
    for asset in sorted(selected, key=lambda item: (item.order_no, str(item.id))):
        if (
            asset.parent_task_id != task_id or asset.deleted_at is not None
            or asset.source_type not in {"PRODUCT_REFERENCE", "SUBJECT_REFERENCE"}
            or not asset.object_key
            or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256 or "")
            or asset.mime_type not in {"image/jpeg", "image/png", "image/webp"}
            or asset.width <= 0 or asset.height <= 0 or asset.size_bytes <= 0
        ):
            raise ValueError("invalid garment reference")
        snapshots.append({
            "asset_id": str(asset.id), "task_id": str(task_id),
            "object_key": asset.object_key, "sha256": asset.sha256,
            "mime_type": asset.mime_type, "size_bytes": asset.size_bytes,
            "width": asset.width, "height": asset.height,
            "original_filename": asset.original_filename,
            "source_type": asset.source_type,
            "view": "unknown", "view_source": "not_recorded",
            "source_image_output_id": str(asset.source_image_output_id)
            if asset.source_image_output_id else None,
            "provenance": "generated_image_reference" if asset.source_image_output_id
            else "uploaded_product_reference_provenance_unverified",
        })
    return snapshots


def validate_reference_snapshot(handoff, assets: list[Asset]) -> list[dict]:
    expected = handoff.manifest_json.get("product_references")
    actual = snapshot_references(assets, handoff.parent_task_id)
    if actual != expected:
        raise ValueError("garment reference changed, expired or revoked")
    return actual
