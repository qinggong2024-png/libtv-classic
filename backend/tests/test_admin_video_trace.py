"""A real saved clip can be traced across other accounts without granting write access."""
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from libtv_agent.domain.enums import (
    AttemptStatus,
    DownloadStatus,
    ParentTaskPhase,
    ValidationStatus,
    VariantKind,
)
from libtv_agent.domain.models import (
    Asset,
    CutflowHandoff,
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    GenerationVariant,
    MaterializationOperation,
    MaterializationSaga,
    MultiCutflowTask,
    ParentTask,
    ValidationResult,
)
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

BASE = "/api/v1/admin/videos"


async def test_admin_trace_links_saved_prompt_references_and_observed_times(
    account_app, tmp_path, monkeypatch,
):
    app, client, ids = account_app
    now = datetime.now(UTC) - timedelta(hours=2)
    async with app.state.database.session_factory() as session:
        task = ParentTask(
            user_id=ids["other"],
            client_task_id="cross-admin-trace",
            title="跨账号生产链路",
            asset_count=2,
            phase=ParentTaskPhase.DELIVERABLE,
            settings_json={
                "generationMode": "automatic",
                "directorMode": "cross_scene_v2",
            },
            compiled_plan_json={
                "compiler": "sol-director",
                "variants": [
                    {
                        "master_prompt": "Saved storyboard plan, not a provider payload.",
                        "creative_idea": "Real scene",
                        "visual_hook": "Show the complete print",
                        "buyer_reason": "Social interaction",
                        "shot_instructions": ["front view", "detail"],
                    }
                ],
            },
            created_at=now,
            analysis_duration_seconds=42.0,
            prompt_analysis_duration_seconds=23.5,
            source_upload_duration_seconds=6.0,
        )
        session.add(task)
        await session.flush()
        asset = Asset(
            parent_task_id=task.id,
            order_no=1,
            original_filename="first-frame.png",
            sha256="a" * 64,
            mime_type="image/png",
            size_bytes=5,
            width=120,
            height=200,
            object_key="safe-uploads/reference1.png",
            thumbnail_key="safe-uploads/reference-thumb.jpg",
            role="STORYBOARD",
            used_for_generation=True,
        )
        unrelated = Asset(
            parent_task_id=task.id,
            order_no=2,
            original_filename="unused.png",
            sha256="b" * 64,
            mime_type="image/png",
            size_bytes=5,
            width=120,
            height=200,
            object_key="safe-uploads/unused.png",
            role="STORYBOARD",
            used_for_generation=True,
        )
        session.add_all([asset, unrelated])
        await session.flush()
        variant = GenerationVariant(
            parent_task_id=task.id,
            variant_type=VariantKind.STABLE,
            variant_index=1,
            prompt_text="This is the currently stored compiled prompt.",
            prompt_hash="d" * 64,
            variant_seed_json={"planner": "Sol"},
            generation_config_json={
                "generationMode": "automatic",
                "directorMode": "cross_scene_v2",
                "cutMode": "explicit_cuts",
                "smartStoryboard": True,
                "multiShot": True,
                "duration": 10,
                "referenceImageBindings": [{
                    "assetId": str(asset.id),
                    "imageId": "image_1",
                    "role": "STORYBOARD",
                }],
            },
            confirmed_at=now + timedelta(seconds=80),
        )
        session.add(variant)
        await session.flush()
        attempt = GenerationAttempt(
            variant_id=variant.id,
            status=AttemptStatus.SUCCEEDED,
            provider="kling",
            provider_model="kling-v3-omni",
            created_at=now + timedelta(seconds=82),
            submission_started_at=now + timedelta(seconds=100),
            submission_finished_at=now + timedelta(seconds=104),
            submitted_at=now + timedelta(seconds=104),
            completed_at=now + timedelta(seconds=178),
        )
        session.add(attempt)
        await session.flush()
        session.add(GenerationCommand(
            attempt_id=attempt.id,
            command_manifest_json={"paidBoundaryPreview": {"credential": "never expose"}},
            created_at=now + timedelta(seconds=90),
        ))
        session.add(GenerationSubmissionAttempt(
            attempt_id=attempt.id, sequence=1, provider="kling",
            idempotency_key=str(uuid.uuid4()),
            status="ACKNOWLEDGED",
            started_at=now + timedelta(seconds=100),
            finished_at=now + timedelta(seconds=104),
        ))
        saga = MaterializationSaga(
            parent_task_id=task.id, project_uuid=str(uuid.uuid4())
        )
        session.add(saga)
        await session.flush()
        session.add(MaterializationOperation(
            saga_id=saga.id,
            step_type="UPLOAD_REFERENCE",
            expected_remote_identity="reference-safe",
            started_at=now + timedelta(seconds=83),
            completed_at=now + timedelta(seconds=88),
        ))
        video = DownloadArtifact(
            attempt_id=attempt.id,
            status=DownloadStatus.READY,
            object_key="video/saved.mp4",
            created_at=now + timedelta(seconds=192),
        )
        session.add(video)
        await session.flush()
        session.add(CutflowHandoff(
            user_id=ids["other"], parent_task_id=task.id,
            variant_id=variant.id, artifact_id=video.id,
            handoff_key=str(uuid.uuid4()),
            status="QUEUED",
            created_at=now + timedelta(seconds=196),
        ))
        session.add(MultiCutflowTask(
            user_id=ids["other"], idempotency_key=str(uuid.uuid4()),
            payload_hash="b" * 64, status="PENDING_REVIEW",
            sources_json=[{"artifactId": str(video.id), "attemptId": str(attempt.id)}],
            created_at=now + timedelta(seconds=198),
        ))
        session.add(ValidationResult(
            attempt_id=attempt.id, status=ValidationStatus.PASSED,
            rule_version="test-version",
            created_at=now + timedelta(seconds=194),
        ))
        await session.commit()
        video_id = str(video.id)
        asset_id = str(asset.id)
        unused_id = str(unrelated.id)

    root = actor(ids, "root")
    trace = await client.get(f"{BASE}/{video_id}/trace", headers=root)
    assert trace.status_code == 200, trace.text
    data = trace.json()
    assert data["direction"]["directorMode"] == "cross_scene_v2"
    assert data["direction"]["smartStoryboard"] is True
    assert data["prompt"]["savedText"] == "This is the currently stored compiled prompt."
    assert data["prompt"]["plan"]["masterPrompt"] == (
        "Saved storyboard plan, not a provider payload."
    )
    assert data["prompt"]["provenance"] == "saved_variant_text_not_immutable_submission_snapshot"
    assert data["references"] == [{
        "id": asset_id,
        "filename": "first-frame.png",
        "role": "STORYBOARD",
        "imageLabel": "image_1",
        "order": 1,
        "usedForGeneration": True,
        "bindingEvidence": "saved_binding",
        "available": True,
    }]
    assert data["durations"][0]["seconds"] == 192
    assert any(event["label"] == "素材准备：UPLOAD_REFERENCE" for event in data["timeline"])
    assert any(event["label"] == "CUT 送剪记录创建" for event in data["timeline"])
    assert any(event["label"] == "CUT 多源送剪记录创建" for event in data["timeline"])
    assert {row["kind"] for row in data["cut"]} == {"single", "multi"}
    assert data["validation"][0]["status"] == "PASSED"
    assert any(event["elapsedSeconds"] == 74 for event in data["timeline"])
    assert all(event["origin"] == "persisted" for event in data["timeline"])
    assert "credential" not in trace.text
    assert "idempotency_key" not in trace.text
    assert "object_key" not in trace.text
    assert any("不能确认" in warning for warning in data["limitations"])

    img = tmp_path / "reference-image.png"
    img.write_bytes(b"fake-image-bytes")
    monkeypatch.setattr(app.state.object_store, "localize", AsyncMock(return_value=img))
    ref = await client.get(f"{BASE}/{video_id}/references/{asset_id}/content", headers=root)
    assert ref.status_code == 200
    assert ref.content == img.read_bytes()
    assert ref.headers["content-type"].startswith("image/png")
    assert "no-store" in ref.headers["cache-control"]
    assert (
        await client.get(f"{BASE}/{video_id}/references/{unused_id}/content", headers=root)
    ).status_code == 404


async def test_video_trace_is_private_to_platform_admin(account_app):
    app, client, ids = account_app
    video_id, asset_id = uuid.uuid4(), uuid.uuid4()
    trace_path = f"{BASE}/{video_id}/trace"
    image_path = f"{BASE}/{video_id}/references/{asset_id}/content"
    for path in (trace_path, image_path):
        assert (await client.get(path)).status_code == 401
        for key in ("member", "manager", "other"):
            response = await client.get(path, headers=actor(ids, key))
            assert response.status_code == 403
        assert (await client.get(path, headers=actor(ids, "root"))).status_code == 404
