"""Real snapshots and files for sibling clips sharing the same product images."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_cutflow_handoff as shared
from libtv_agent.domain.models import (
    Asset,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
)
from libtv_agent.services.cutflow_handoff import CutflowHandoffError
from libtv_agent.services.cutflow_multi import (
    MultiCutflowError,
    create_multi_task,
    snapshot_sources,
)
from libtv_agent.services.cutflow_multi_adapter import build_multi_manifest
from libtv_agent.services.cutflow_multi_worker import publish_multi_package
from libtv_agent.services.cutflow_product_package import native_product_folder
from PIL import Image

handoff_env = shared.handoff_env


@pytest.fixture
async def sibling_products(handoff_env):
    database, settings, store, ids = handoff_env
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        artifact.size_bytes = len(b"generated-video")
        artifact.duration_seconds = 3.5
        first = await session.get(GenerationVariant, ids["variant"])
        other = GenerationVariant(
            id=uuid.uuid4(), parent_task_id=ids["task"],
            variant_type=first.variant_type, revision=2,
            generation_status=first.generation_status, download_status=first.download_status,
            validation_status=first.validation_status,
        )
        attempt = GenerationAttempt(
            id=uuid.uuid4(), variant_id=other.id, status="SUCCEEDED", provider="kling",
        )
        session.add_all([other, attempt, DownloadArtifact(
            id=uuid.uuid4(), attempt_id=attempt.id, status="READY", revision=1,
            object_key=artifact.object_key, sha256=artifact.sha256,
            size_bytes=artifact.size_bytes, duration_seconds=3.5,
        )])
        assets = []
        for index, name in enumerate(["样衣25861_正面.webp", "样衣25861_背面.webp"], 1):
            image = settings.artifact_root / f"product-{index}.jpg"
            Image.new("RGB", (64, 64), (index * 40, 80, 90)).save(image)
            raw = image.read_bytes()
            key = await store.put_file(image, f"products/{index}.jpg", "image/jpeg")
            asset = Asset(
                id=uuid.uuid4(), parent_task_id=ids["task"], role="PRODUCT", order_no=index,
                original_filename=name, object_key=key, mime_type="image/jpeg",
                sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), width=64, height=64,
            )
            session.add(asset)
            assets.append(asset)
        await session.commit()
        sources = await snapshot_sources(session, ids["user"], [first.id, other.id])
    return sources, assets


async def test_sibling_references_are_unique_bound_and_stable(handoff_env, sibling_products):
    sources, assets = sibling_products
    refs = [ref for source in sources for ref in source["productReferences"]]
    assert len({uuid.UUID(ref["refId"]) for ref in refs}) == 4
    assert {ref["assetId"] for ref in refs} == {str(asset.id) for asset in assets}
    async with handoff_env[0].session_factory() as session:
        reversed_sources = await snapshot_sources(
            session, handoff_env[3]["user"],
            [uuid.UUID(source["variantId"]) for source in reversed(sources)],
        )
    assert {
        source["clipId"]: [ref["refId"] for ref in source["productReferences"]]
        for source in sources
    } == {
        source["clipId"]: [ref["refId"] for ref in source["productReferences"]]
        for source in reversed_sources
    }


@pytest.mark.parametrize("corrupt", [None, "reference", "native-image", "native-video"])
async def test_native_folder_and_v2_package_publish_atomically(
    handoff_env, sibling_products, corrupt,
):
    _database, settings, store, ids = handoff_env
    sources, _assets = sibling_products
    task = SimpleNamespace(
        id=uuid.uuid4(), user_id=ids["user"], created_at=datetime.now(UTC), sources_json=sources,
    )
    descriptor, digest = await publish_multi_package(task, settings, store)
    package = settings.cutflow_handoff_root / "inbox" / f"multi-{task.id}"
    manifest_raw = (package / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == digest
    ready_raw = (package / "READY").read_bytes()
    assert json.loads(ready_raw) == {"sha256": digest}
    manifest = json.loads(manifest_raw)
    assert descriptor["nativeIntake"] == manifest["nativeIntake"]
    native = manifest["nativeIntake"]
    assert native["relativePath"] == "cut-native"
    assert len(native["groups"]) == 1
    group = native["groups"][0]
    assert len(group["videos"]) == 2
    assert len(group["productImages"]) == 2
    files = [*group["videos"], *group["productImages"]]
    assert len(list((package / "cut-native").rglob("*.*"))) == 4
    for item in files:
        relative = Path(item["relativePath"])
        assert relative.parts[1] == group["folder"]
        assert relative.name.startswith(group["folder"] + "_")
        raw = (package / relative).read_bytes()
        assert len(raw) == item["size"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    assert all(image["relativePath"].endswith(".jpg") for image in group["productImages"])
    assert "_front_" in group["productImages"][0]["relativePath"]
    assert "_back_" in group["productImages"][1]["relativePath"]
    for source in manifest["sources"]:
        bound = [ref for ref in manifest["productReferences"]
                 if ref["sourceOrder"] == source["order"]]
        assert source["productReferenceIds"] == [ref["refId"] for ref in bound]
        for ref in bound:
            assert ref["relativePath"].startswith(f"references/{source['order']:02d}/")
            assert hashlib.sha256((package / ref["relativePath"]).read_bytes()).hexdigest() == (
                ref["sha256"]
            )
    if corrupt:
        relative = {
            "reference": manifest["productReferences"][0]["relativePath"],
            "native-image": group["productImages"][0]["relativePath"],
            "native-video": group["videos"][0]["relativePath"],
        }[corrupt]
        (package / relative).write_bytes(b"corrupted")
        with pytest.raises(CutflowHandoffError) as error:
            await publish_multi_package(task, settings, store)
        assert error.value.code == "CUTFLOW_SOURCE_INVALID"
        assert (package / relative).read_bytes() == b"corrupted"
    else:
        assert await publish_multi_package(task, settings, store) == (descriptor, digest)
    assert (package / "manifest.json").read_bytes() == manifest_raw
    assert (package / "READY").read_bytes() == ready_raw
    assert not list((package.parent).glob(".m-*.tmp"))


async def test_duplicate_snapshot_binding_rejected_before_nas_write(
    handoff_env, sibling_products,
):
    sources, _assets = sibling_products
    sources[1]["productReferences"][0]["refId"] = sources[0]["productReferences"][0]["refId"]
    task = SimpleNamespace(sources_json=sources)
    with pytest.raises(CutflowHandoffError) as error:
        await publish_multi_package(task, handoff_env[1], handoff_env[2])
    assert error.value.code == "INVALID_PRODUCT_REFERENCE"
    assert not handoff_env[1].cutflow_handoff_root.exists()


async def test_missing_product_image_rejected_before_task_creation(handoff_env):
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        artifact.size_bytes = 15
        artifact.duration_seconds = 3.5
        with pytest.raises(MultiCutflowError) as error:
            await create_multi_task(
                session, ids["user"], [ids["variant"]], "missing-image", True,
                allow_single_source=True,
            )
        assert error.value.code == "PRODUCT_REFERENCE_REQUIRED"


def test_product_folders_are_safe_short_and_do_not_merge_by_name():
    name = "../../CON:同款正面.webp"
    first = native_product_folder("task-1", name)
    assert first != native_product_folder("task-2", name)
    assert first == native_product_folder("task-1", name)
    assert len(first) <= 19
    assert all(char not in first for char in '/\\:<>|?*')


def test_legacy_snapshot_does_not_acquire_a_new_native_manifest_field():
    from test_cutflow_multi_adapter import task_fixture

    assert "nativeIntake" not in build_multi_manifest(task_fixture())


async def test_native_export_recovers_legacy_duplicate_refs_without_rewriting_input(
    handoff_env, sibling_products, tmp_path,
):
    from scripts.export_cut_native_folder import export_native_folder

    sources, _assets = sibling_products
    _database, settings, store, ids = handoff_env
    task = SimpleNamespace(
        id=uuid.uuid4(), user_id=ids["user"], created_at=datetime.now(UTC), sources_json=sources,
    )
    await publish_multi_package(task, settings, store)
    package = settings.cutflow_handoff_root / "inbox" / f"multi-{task.id}"
    # Reproduce a historical sender's global asset IDs in an isolated fixture.
    manifest = json.loads((package / "manifest.json").read_bytes())
    for ref in manifest["productReferences"]:
        ref["refId"] = ref.pop("assetId")
    for source in manifest["sources"]:
        source["productReferenceIds"] = [ref["refId"] for ref in manifest["productReferences"]
                                         if ref["sourceOrder"] == source["order"]]
    raw = json.dumps(manifest).encode()
    (package / "manifest.json").write_bytes(raw)
    ready = json.dumps({"sha256": hashlib.sha256(raw).hexdigest()}).encode()
    (package / "READY").write_bytes(ready)
    destination = tmp_path / "native-export"
    plan = export_native_folder(package, destination)
    assert not destination.exists()
    assert plan["mediaFileCount"] == 4
    receipt = export_native_folder(package, destination, apply=True)
    assert len(receipt["groups"]) == 1
    group = receipt["groups"][0]
    assert len(group["videos"]) == len(group["productImages"]) == 2
    for item in [*group["videos"], *group["productImages"]]:
        file = destination / item["relativePath"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
        assert Path(item["relativePath"]).parts[0] == group["folder"]
    assert export_native_folder(package, destination, apply=True) == receipt
    assert (package / "manifest.json").read_bytes() == raw
    assert (package / "READY").read_bytes() == ready
    damaged = destination / group["videos"][0]["relativePath"]
    damaged.write_bytes(b"broken")
    with pytest.raises(ValueError, match="incomplete or changed"):
        export_native_folder(package, destination, apply=True)
    assert damaged.read_bytes() == b"broken"
    with pytest.raises(ValueError, match="outside the immutable"):
        export_native_folder(package, package / "nested-output", apply=True)
