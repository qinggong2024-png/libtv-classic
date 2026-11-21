"""Offline automatic-policy contracts; no production, NAS, provider or CUT calls."""

import asyncio
import json
import shutil
import subprocess
import uuid

import pytest
import test_cutflow_handoff as shared
from libtv_agent.api.cutflow_handoffs import _read
from libtv_agent.domain.models import Asset, CutflowHandoff, DownloadArtifact, GenerationVariant
from libtv_agent.runner import cutflow_worker
from libtv_agent.services.cutflow_auto_submit import enqueue_completed
from libtv_agent.services.cutflow_handoff import (
    CutflowHandoffError,
    handoff_identity,
    publish_package,
    sha256_file,
)
from libtv_agent.services.cutflow_policy import AUTO_CONTRACT, automatic_policy, saved_policy
from sqlalchemy import func, select

base_handoff_env = shared.handoff_env
enqueue = shared.enqueue
TEMPLATE = "73afa4b6-028c-49cd-af57-f34cc4d99cf0"


@pytest.fixture
async def handoff_env(base_handoff_env, tmp_path):
    from PIL import Image

    database, _settings, store, ids = base_handoff_env
    image = tmp_path / "garment.jpg"
    Image.new("RGB", (80, 120), "white").save(image)
    key = await store.put_file(image, "inputs/garment.jpg", "image/jpeg")
    ids["product"] = uuid.uuid4()
    async with database.session_factory() as session:
        session.add(Asset(
            id=ids["product"], parent_task_id=ids["task"], role="PRODUCT",
            source_type="PRODUCT_REFERENCE", order_no=1, original_filename="garment.jpg",
            sha256=sha256_file(image), mime_type="image/jpeg", size_bytes=image.stat().st_size,
            width=80, height=120, object_key=key,
        ))
        await session.commit()
    return base_handoff_env


def enable(settings):
    settings.cutflow_auto_edit_enabled = True
    settings.cutflow_ootd_template_id = TEMPLATE


async def test_snapshot_survives_configuration_change_and_idempotent_submit(handoff_env):
    database, settings, store, ids = handoff_env
    enable(settings)
    row = await enqueue(handoff_env)
    expected = automatic_policy(TEMPLATE, ids["user"])
    assert row.contract_version == AUTO_CONTRACT
    assert saved_policy(row) == expected
    settings.cutflow_ootd_template_id = str(uuid.uuid4())
    same = await enqueue(handoff_env)
    assert same.id == row.id and saved_policy(same) == expected
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        references = list(await session.scalars(select(Asset).where(
            Asset.parent_task_id == ids["task"], Asset.role == "PRODUCT",
        )))
        descriptor = await publish_package(
            row, artifact, settings, store, product_references=references,
        )
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["schema_version"] == AUTO_CONTRACT
    assert manifest["edit_intent"] == {"preserve_source_audio": artifact.has_audio}
    assert manifest["edit_policy"] == descriptor["editPolicy"] == expected
    assert descriptor["manifestSha256"] == sha256_file(package / "manifest.json")
    reference = manifest["product_references"][0]
    assert reference["role"] == "product_reference"
    assert reference["applies_to_asset_id"] == str(ids["variant"])
    assert reference["sha256"] == sha256_file(package / reference["relative_path"])
    assert "object_key" not in reference
    assert reference["provenance"] == "uploaded_product_reference_provenance_unverified"
    row.manifest_path = f"inbox/{row.handoff_key}/manifest.json"
    row.manifest_json = descriptor
    assert saved_policy(row) == expected
    assert _read(row)["editMode"] == "automatic"


async def test_existing_manual_job_is_not_implicitly_reapproved(handoff_env):
    row = await enqueue(handoff_env)
    enable(handoff_env[1])
    same = await enqueue(handoff_env)
    assert same.id == row.id and saved_policy(same) is None
    assert _read(same)["editMode"] == "manual"


@pytest.mark.parametrize("template", [None, "ootd", "00000000-0000-0000-0000-000000000000"])
async def test_invalid_binding_stops_before_enqueue_and_nas(handoff_env, template):
    database, settings, _store, _ids = handoff_env
    enable(settings)
    settings.cutflow_ootd_template_id = template
    with pytest.raises(CutflowHandoffError, match="母版"):
        await enqueue(handoff_env)
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 0
    assert not settings.cutflow_handoff_root.exists()


@pytest.mark.parametrize("change", ["owner", "manual-contract", "missing", "mode"])
async def test_invalid_policy_cannot_reach_transfer(handoff_env, change):
    enable(handoff_env[1])
    row = await enqueue(handoff_env)
    policy = dict(row.manifest_json["edit_policy"])
    if change == "owner":
        policy["requestedBy"] = str(uuid.uuid4())
    elif change == "manual-contract":
        row.contract_version = "libtv-to-cutflow/1.1"
    elif change == "mode":
        policy["mode"] = "manual"
    row.manifest_json = {
        **row.manifest_json, "edit_policy": None if change == "missing" else policy,
    }
    with pytest.raises(CutflowHandoffError) as caught:
        handoff_identity(row)
    assert caught.value.code == "CUTFLOW_POLICY_INVALID"


@pytest.mark.parametrize("ack", ["matching", "missing", "wrong-template", "wrong-hash"])
async def test_worker_requires_bound_remote_acknowledgement(handoff_env, monkeypatch, ack):
    database, settings, store, ids = handoff_env
    enable(settings)
    row = await enqueue(handoff_env)
    batch_id = str(uuid.uuid4())
    descriptor = {}

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            descriptor.update(payload)
        response = {
            "handoffId": row.handoff_key, "batchId": batch_id,
            "status": "batch_queued", "outputs": [],
        }
        if ack != "missing" and descriptor:
            policy = dict(descriptor["editPolicy"])
            if ack == "wrong-template":
                policy["templateId"] = str(uuid.uuid4())
            response["editPolicyReceipt"] = {
                "state": "accepted", "policy": policy,
                "manifestSha256": "a" * 64 if ack == "wrong-hash"
                else descriptor["manifestSha256"],
            }
        return response

    async def forbidden_import(*args, **kwargs):
        pytest.fail("Unaccepted automatic policy reached output import")

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    if ack != "matching":
        monkeypatch.setattr(cutflow_worker, "import_outputs", forbidden_import)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved_policy(saved) == automatic_policy(TEMPLATE, ids["user"])
        if ack == "matching":
            assert saved.status == "EDITING" and saved.error_code is None
            assert saved.cutflow_batch_id == batch_id
        else:
            assert saved.error_code == "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED"
            assert saved.cutflow_batch_id is None


@pytest.mark.parametrize("code,expected", [
    ("NO_MATCHING_HOOK", "CUTFLOW_NO_MATCHING_HOOK"),
    ("TEMPLATE_NOT_READY", "CUTFLOW_TEMPLATE_NOT_READY"),
    ("QUALITY_REJECTED", "CUTFLOW_QUALITY_REJECTED"),
    ("SOURCE_INSUFFICIENT", "CUTFLOW_SOURCE_INSUFFICIENT"),
    ("PRIVATE_PATH_OR_TOKEN", "CUTFLOW_EDIT_FAILED"),
])
async def test_editing_failure_returns_safe_actionable_error_without_retry(
    handoff_env, monkeypatch, code, expected,
):
    database, settings, store, _ids = handoff_env
    enable(settings)
    row = await enqueue(handoff_env)
    descriptor = {}

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            descriptor.update(payload)
        return {
            "handoffId": row.handoff_key, "status": "failed", "outputs": [],
            "error": {"code": code, "message": "PRIVATE_PATH_OR_TOKEN"},
            "editPolicyReceipt": {
                "state": "accepted", "policy": descriptor["editPolicy"],
                "manifestSha256": descriptor["manifestSha256"],
            },
        }

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "FAILED" and saved.error_code == expected
        assert saved.failure_count == 1 and saved.next_run_at is None
        assert "PRIVATE_PATH_OR_TOKEN" not in saved.error_message
    assert not await cutflow_worker.tick(database, settings, store)


@pytest.mark.parametrize("change", ["storyboard", "deleted", "wrong-task", "missing-key"])
async def test_automatic_enqueue_requires_current_task_product_reference(handoff_env, change):
    from datetime import UTC, datetime

    database, settings, _store, ids = handoff_env
    enable(settings)
    async with database.session_factory() as session:
        reference = await session.get(Asset, ids["product"])
        if change == "storyboard":
            reference.role = "STORYBOARD"
            reference.source_type = "STORYBOARD"
        elif change == "deleted":
            reference.deleted_at = datetime.now(UTC)
        elif change == "wrong-task":
            reference.parent_task_id = uuid.uuid4()
        else:
            reference.object_key = None
        await session.commit()
    with pytest.raises(CutflowHandoffError) as caught:
        await enqueue(handoff_env)
    assert caught.value.code == "CUTFLOW_PRODUCT_REFERENCE_REQUIRED"
    assert not settings.cutflow_handoff_root.exists()


@pytest.mark.parametrize("change", ["revoked", "corrupt", "changed"])
async def test_reference_change_or_corruption_never_publishes_ready(handoff_env, change):
    from datetime import UTC, datetime

    database, settings, store, ids = handoff_env
    enable(settings)
    row = await enqueue(handoff_env)
    async with database.session_factory() as session:
        reference = await session.get(Asset, ids["product"])
        if change == "revoked":
            reference.deleted_at = datetime.now(UTC)
        elif change == "changed":
            reference.sha256 = "a" * 64
        else:
            image = await store.localize(reference.object_key, settings.artifact_root / "unused")
            image.write_bytes(b"corrupt")
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        with pytest.raises(CutflowHandoffError):
            await publish_package(row, artifact, settings, store, product_references=[reference])
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    assert not (package / "READY").exists()


async def test_fixed_video_subject_reference_and_generated_provenance(handoff_env):
    database, settings, store, ids = handoff_env
    enable(settings)
    async with database.session_factory() as session:
        reference = await session.get(Asset, ids["product"])
        reference.source_type = "SUBJECT_REFERENCE"
        reference.source_image_output_id = uuid.uuid4()
        await session.commit()
    row = await enqueue(handoff_env)
    async with database.session_factory() as session:
        reference = await session.get(Asset, ids["product"])
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        await publish_package(row, artifact, settings, store, product_references=[reference])
        assert await store.exists(reference.object_key)
    package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
    manifest = json.loads((package / "manifest.json").read_text())
    reference = manifest["product_references"][0]
    assert reference["source_type"] == "SUBJECT_REFERENCE"
    assert reference["provenance"] == "generated_image_reference"
    assert reference["source_image_output_id"]


async def test_automatic_reference_package_and_real_video_return_roundtrip(
    handoff_env, monkeypatch,
):
    """Simulated CUT control response; real package/image/MP4 bytes and durable import."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    database, settings, store, ids = handoff_env
    enable(settings)
    video = settings.artifact_root / "isolated-source.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        subprocess.run,
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x288:rate=10",
         "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True, timeout=30, capture_output=True,
    )
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        artifact.object_key = await store.put_file(video, "outputs/real.mp4", "video/mp4")
        artifact.sha256 = sha256_file(video)
        variant = await session.get(GenerationVariant, ids["variant"])
        source_approval = variant.final_approval_status
        await session.commit()
    row = await enqueue(handoff_env)
    batch_id, output_id = str(uuid.uuid4()), str(uuid.uuid4())
    descriptor = {}

    def remote(_settings, method, _suffix, payload=None):
        if method == "POST":
            descriptor.update(payload)
        package = settings.cutflow_handoff_root / "inbox" / row.handoff_key
        manifest = json.loads((package / "manifest.json").read_text())
        assert sha256_file(package / "manifest.json") == descriptor["manifestSha256"]
        assert json.loads((package / "READY").read_text())["sha256"] == descriptor["manifestSha256"]
        assert manifest["edit_policy"]["garmentCheck"] == "exclude_mismatch_review_uncertain"
        reference = manifest["product_references"][0]
        assert sha256_file(package / reference["relative_path"]) == reference["sha256"]
        output = settings.cutflow_handoff_root / "outbox" / row.handoff_key / "outputs/edited.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Fixture copies a real MP4, and does not claim to implement CUT editing/garment checks.
        shutil.copyfile(video, output)
        return {
            "handoffId": row.handoff_key, "batchId": batch_id, "status": "completed",
            "editPolicyReceipt": {
                "state": "accepted", "policy": descriptor["editPolicy"],
                "manifestSha256": descriptor["manifestSha256"],
            },
            "outputs": [{"id": output_id, "relative_path": "outputs/edited.mp4",
                         "sha256": sha256_file(output), "size_bytes": output.stat().st_size}],
        }

    monkeypatch.setattr(cutflow_worker, "control_request", remote)
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert saved.status == "PENDING_REVIEW", saved.error_message
        assert saved.cutflow_batch_id == batch_id
        assert len(saved.outputs_json) == 1
        result = saved.outputs_json[0]
        assert result["review_status"] == "PENDING" and result["width"] == 160
        assert await store.exists(result["object_key"])
        assert await session.scalar(select(func.count()).select_from(DownloadArtifact)) == 1
        variant = await session.get(GenerationVariant, ids["variant"])
        assert variant.final_approval_status == source_approval
    # Replay an unchanged receipt without adding outputs or changing review state.
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        saved.next_run_at = None
        await session.commit()
    await cutflow_worker.tick(database, settings, store)
    async with database.session_factory() as session:
        saved = await session.get(CutflowHandoff, row.id)
        assert len(saved.outputs_json) == 1
        assert saved.outputs_json[0]["review_status"] == "PENDING"


def test_fixed_video_task_auto_cutflow_payload_defaults_to_true_and_accepts_false():
    from libtv_agent.domain.schemas import VideoTaskCreate

    payload = {
        "clientTaskId": "cutflow-option-contract",
        "title": "video task",
        "storyboards": [{"uploadId": str(uuid.uuid4()), "order": 1}],
        "generationMode": "custom",
        "customPrompt": "Natural movement",
        "paidSubmissionConsent": True,
    }
    assert VideoTaskCreate.model_validate(payload).auto_submit_to_cutflow is True
    assert VideoTaskCreate.model_validate({
        **payload, "autoSubmitToCutflow": False,
    }).auto_submit_to_cutflow is False


@pytest.mark.parametrize("setting, expected", [
    (True, 1),
    (False, 0),
    (None, 1),
])
async def test_auto_submit_respects_task_level_opt_out(handoff_env, setting, expected):
    from datetime import UTC, datetime, timedelta

    from libtv_agent.domain.models import ParentTask

    database, settings, store, ids = handoff_env
    enable(settings)
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(days=1)
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.settings_json = ({"autoSubmitToCutflow": setting}
                              if setting is not None else {})
        await session.commit()

    assert await enqueue_completed(database, settings, store) == expected
    # A fresh worker scan must neither override an opt-out nor create duplicates.
    assert await enqueue_completed(database, settings, store) == 0
    async with database.session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(CutflowHandoff)
        ) == expected
    if not expected:
        # Manual submission must remain possible for opted-out tasks.
        await enqueue(handoff_env)
        async with database.session_factory() as session:
            assert await session.scalar(
                select(func.count()).select_from(CutflowHandoff)
            ) == 1


async def test_opt_out_blocks_every_variant_of_a_multi_result_task(handoff_env):
    from datetime import UTC, datetime, timedelta

    from libtv_agent.domain.enums import (
        AttemptStatus, DownloadStatus, GenerationStatus, ValidationStatus, VariantKind,
    )
    from libtv_agent.domain.models import GenerationAttempt, ParentTask

    database, settings, store, ids = handoff_env
    enable(settings)
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(days=1)
    async with database.session_factory() as session:
        task = await session.get(ParentTask, ids["task"])
        task.settings_json = {"autoSubmitToCutflow": False, "resultCount": 2}
        original = await session.get(DownloadArtifact, ids["artifact"])
        new_variant_id, new_attempt_id = uuid.uuid4(), uuid.uuid4()
        session.add_all([
            GenerationVariant(
                id=new_variant_id, parent_task_id=ids["task"],
                variant_type=VariantKind.DYNAMIC,
                generation_status=GenerationStatus.SUCCEEDED,
                download_status=DownloadStatus.READY,
                validation_status=ValidationStatus.PASSED,
            ),
            GenerationAttempt(
                id=new_attempt_id, variant_id=new_variant_id,
                status=AttemptStatus.SUCCEEDED, provider="kling",
            ),
            DownloadArtifact(
                id=uuid.uuid4(), attempt_id=new_attempt_id,
                status=DownloadStatus.READY, object_key=original.object_key,
                sha256=original.sha256, revision=1,
            ),
        ])
        await session.commit()
    assert await enqueue_completed(database, settings, store) == 0
    async with database.session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(CutflowHandoff)
        ) == 0


async def test_pending_source_review_auto_submits_without_changing_review(handoff_env):
    from datetime import UTC, datetime, timedelta

    from libtv_agent.domain.enums import QualityDecisionStatus
    from libtv_agent.domain.models import ParentTask

    database, settings, store, ids = handoff_env
    enable(settings)
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(days=1)
    async with database.session_factory() as session:
        variant = await session.get(GenerationVariant, ids["variant"])
        variant.final_approval_status = QualityDecisionStatus.PENDING
        await session.commit()
    assert await enqueue_completed(database, settings, store) == 1
    assert await enqueue_completed(database, settings, store) == 0
    async with database.session_factory() as session:
        row = await session.scalar(select(CutflowHandoff))
        assert saved_policy(row)["sourceApproval"] == "not_required"
        assert row.manifest_json["source"]["source_approval_required"] is False
        variant = await session.get(GenerationVariant, ids["variant"])
        assert variant.final_approval_status == QualityDecisionStatus.PENDING
        task = await session.get(ParentTask, ids["task"])
        assert task.user_id == row.user_id


async def test_auto_submit_requires_explicit_cutover_and_never_drains_old_tasks(handoff_env):
    from datetime import UTC, datetime, timedelta

    database, settings, store, _ids = handoff_env
    enable(settings)
    assert await enqueue_completed(database, settings, store) == 0
    settings.cutflow_auto_submit_after = datetime.now(UTC) + timedelta(days=1)
    assert await enqueue_completed(database, settings, store) == 0
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CutflowHandoff)) == 0


async def test_auto_submit_does_not_grant_readonly_user_write_access(handoff_env):
    from datetime import UTC, datetime, timedelta

    from libtv_agent.domain.enums import UserRole
    from libtv_agent.domain.models import User

    database, settings, store, ids = handoff_env
    enable(settings)
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(days=1)
    async with database.session_factory() as session:
        actor = await session.get(User, ids["user"])
        actor.role = UserRole.AUDITOR
        await session.commit()
    assert await enqueue_completed(database, settings, store) == 0


async def test_auto_submit_respects_revoked_make_page_grant(handoff_env):
    from datetime import UTC, datetime, timedelta

    from libtv_agent.domain.models import User

    database, settings, store, ids = handoff_env
    enable(settings)
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(days=1)
    async with database.session_factory() as session:
        actor = await session.get(User, ids["user"])
        actor.page_permissions_json = ["records"]
        await session.commit()
    assert await enqueue_completed(database, settings, store) == 0
