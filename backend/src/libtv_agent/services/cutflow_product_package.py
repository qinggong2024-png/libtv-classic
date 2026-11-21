"""Product folders for CUT's native scanner, alongside the immutable LAN package."""

import hashlib
import re
import uuid
from pathlib import Path

NATIVE_ROOT = "cut-native"
IMAGE_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PRODUCT_REFERENCES = 5
MAX_PRODUCT_REFERENCE_BYTES = 5 * 1024 * 1024


def product_reference_id(clip_id: str, asset_id: str) -> str:
    # The same asset legitimately belongs to multiple clips. CUT requires a
    # globally unique UUID for each source binding, rather than an asset UUID.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"classic-cut-reference:{clip_id}:{asset_id}"))


def native_product_folder(task_id: str, filename: str) -> str:
    # A parent generation task is a proven product-image binding. Do not merge
    # unrelated tasks merely because their brand or human title matches. Keep
    # paths short for the existing long Windows UNC handoff root.
    stem = re.sub(r"[^\w-]+", "-", Path(filename).stem, flags=re.UNICODE).strip("-_. ")
    key = hashlib.sha256(task_id.encode()).hexdigest()[:8]
    return f"p-{stem[:8] or 'product'}-{key}"


def native_reference_path(folder: str, index: int, filename: str, mime_type: str) -> str:
    kind = "product"
    for pattern, label in (
        (r"正面?|front", "front"),
        (r"反面?|背面?|back", "back"),
        (r"logo|标志|文字|text", "logo"),
        (r"图案|印花|细节|detail", "detail"),
    ):
        if re.search(pattern, filename, flags=re.IGNORECASE):
            kind = label
            break
    return f"{NATIVE_ROOT}/{folder}/{folder}_{kind}_{index:02d}{IMAGE_SUFFIXES[mime_type]}"


def native_intake(sources: list[dict]) -> dict | None:
    """Describe only new snapshots; old published manifests keep identical bytes."""
    groups = {}
    for source in sorted(sources, key=lambda item: item["order"]):
        folder = source.get("nativeProductFolder")
        if not folder:
            continue
        group = groups.setdefault(folder, {
            "folder": folder, "sourceTaskId": source["taskId"],
            "title": source.get("title"), "videos": [], "productImages": [],
        })
        if group["sourceTaskId"] != source["taskId"]:
            raise ValueError("CUT native product folder collision")
        group["videos"].append({
            "clipId": source["clipId"], "relativePath": source["nativeRelativePath"],
            "sha256": source["sha256"], "size": source["sizeBytes"],
        })
        for reference in source["productReferences"]:
            image = {
                "assetId": reference["assetId"],
                "filename": reference["filename"],
                "relativePath": reference["nativeRelativePath"],
                "sha256": reference["sha256"], "size": reference["sizeBytes"],
            }
            if image not in group["productImages"]:
                group["productImages"].append(image)
    return {
        "relativePath": NATIVE_ROOT, "groupingMethod": "product_directory",
        "groups": list(groups.values()),
    } if groups else None
