"""Copy an immutable classic package into CUT's native product-folder layout.

This publishes files only. It never submits a CUT task or rewrites the input.
Select the resulting destination as the batch folder in CUT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from libtv_agent.services.cutflow_handoff import (  # noqa: E402
    copy_verified,
    safe_path,
    sha256_file,
)
from libtv_agent.services.cutflow_product_package import (  # noqa: E402
    MAX_PRODUCT_REFERENCE_BYTES,
    MAX_PRODUCT_REFERENCES,
    NATIVE_ROOT,
    native_intake,
    native_product_folder,
    native_reference_path,
)


def export_native_folder(package: Path, destination: Path, *, apply: bool = False) -> dict:
    if destination.resolve().is_relative_to(package.resolve()):
        raise ValueError("Destination must be outside the immutable input package")
    raw = (package / "manifest.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    ready = json.loads((package / "READY").read_text(encoding="utf-8"))
    if ready.get("sha256") != digest:
        raise ValueError("READY does not match the raw manifest bytes")
    manifest = json.loads(raw)
    snapshots = []
    copies = {}
    for source in sorted(manifest["sources"], key=lambda item: item["order"]):
        references = [ref for ref in manifest.get("productReferences", [])
                      if ref["sourceOrder"] == source["order"]]
        if not 1 <= len(references) <= MAX_PRODUCT_REFERENCES:
            raise ValueError("Each source needs one to five product images")
        ref_ids = [ref["refId"] for ref in references]
        if len(set(ref_ids)) != len(ref_ids) or set(ref_ids) != set(source["productReferenceIds"]):
            raise ValueError("Source product-image membership does not match")
        task_id = source["source"]["task_id"]
        folder = native_product_folder(task_id, references[0]["filename"])
        snapshot = {
            "taskId": task_id, "clipId": source["clipId"], "order": source["order"],
            "sha256": source["sha256"], "sizeBytes": source["size"],
            "relativePath": source["relativePath"], "nativeProductFolder": folder,
            "nativeRelativePath": f"{NATIVE_ROOT}/{folder}/{folder}_{source['order']:02d}.mp4",
            "productReferences": [],
        }
        for index, ref in enumerate(references, 1):
            image_path = safe_path(package, ref["relativePath"])
            if not 0 < ref["size"] <= MAX_PRODUCT_REFERENCE_BYTES:
                raise ValueError("Product image exceeds CUT's 5 MiB limit")
            with Image.open(image_path) as image:
                mime = Image.MIME.get(image.format)
                if (mime != ref["mimeType"] or image.size != (ref["width"], ref["height"])):
                    raise ValueError("Decoded product image does not match its declaration")
                image.load()
            snapshot["productReferences"].append({
                "assetId": ref.get("assetId", ref["refId"]), "filename": ref["filename"],
                "relativePath": ref["relativePath"],
                "nativeRelativePath": native_reference_path(folder, index, ref["filename"], mime),
                "sha256": ref["sha256"], "sizeBytes": ref["size"],
            })
        for item in [snapshot, *snapshot["productReferences"]]:
            original = safe_path(package, item["relativePath"])
            if (original.stat().st_size != item["sizeBytes"]
                    or sha256_file(original) != item["sha256"]):
                raise ValueError("Source package media hash or size does not match")
            relative = item["nativeRelativePath"].removeprefix(f"{NATIVE_ROOT}/")
            entry = (original, item["sha256"], item["sizeBytes"])
            previous = copies.get(relative)
            if previous and previous[1:] != entry[1:]:
                raise ValueError("Native product filename collision")
            copies[relative] = entry
        snapshots.append(snapshot)
    layout = native_intake(snapshots)
    if layout is None:
        raise ValueError("No media to export")
    # Paths in the receipt are relative to the scanner's selected batch root.
    for group in layout["groups"]:
        for item in [*group["videos"], *group["productImages"]]:
            item["relativePath"] = item["relativePath"].removeprefix(f"{NATIVE_ROOT}/")
    receipt = {
        "schemaVersion": "classic-cut-native/1",
        "sourceTaskId": manifest["taskId"], "sourceManifestSha256": digest,
        "groupingMethod": "product_directory", "groups": layout["groups"],
    }
    receipt_raw = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    if destination.exists():
        if (destination / "classic-native-layout.json").read_bytes() != receipt_raw:
            raise ValueError("Destination exists with different contents; refusing to replace it")
        for relative, (_original, expected, size) in copies.items():
            file = safe_path(destination, relative)
            if file.stat().st_size != size or sha256_file(file) != expected:
                raise ValueError("Existing native export is incomplete or changed")
    elif apply:
        # Staging and destination share a parent, so the final rename is atomic.
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".native-{uuid.uuid4().hex[:12]}"
        staging.mkdir()
        try:
            for relative, (original, expected, size) in copies.items():
                copied = copy_verified(original, safe_path(staging, relative), expected, size)
                if copied != size:
                    raise ValueError("Native media size does not match")
            (staging / "classic-native-layout.json").write_bytes(receipt_raw)
            os.rename(staging, destination)
        finally:
            if staging.exists():
                resolved = staging.resolve()
                if resolved.parent != destination.parent.resolve() or not resolved.name.startswith(
                    ".native-",
                ):
                    raise ValueError("Unexpected staging cleanup location")
                shutil.rmtree(resolved)
    return {
        **receipt, "destination": str(destination), "applied": apply,
        "mediaFileCount": len(copies), "totalBytes": sum(item[2] for item in copies.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(export_native_folder(
        args.package, args.destination, apply=args.apply,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
