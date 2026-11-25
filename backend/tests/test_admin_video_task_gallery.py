"""Admin gallery groups A/B as one task and plays imported CUT output only."""
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from libtv_agent.domain.enums import AttemptStatus, DownloadStatus, ParentTaskPhase, VariantKind
from libtv_agent.domain.models import (
    CutflowHandoff, DownloadArtifact, GenerationAttempt, GenerationVariant, MultiCutflowTask, ParentTask,
)
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

BASE = "/api/v1/admin/video-tasks"


async def test_task_gallery_groups_two_clips_and_plays_cut_without_approval(account_app, tmp_path, monkeypatch):
    app, client, ids = account_app
    video = tmp_path / "cut-test.mp4"
    video.write_bytes(b"test-only-cut-video")
    parent_id = uuid.uuid4()
    artifacts = []
    async with app.state.database.session_factory() as session:
        session.add(ParentTask(
            id=parent_id, user_id=ids["member"],
            client_task_id="test-gallery-a-b", title="两条原片一条成片",
            asset_count=2, phase=ParentTaskPhase.DELIVERABLE,
        ))
        await session.flush()
        for index in (1, 2):
            kind = VariantKind.STABLE
            variant = GenerationVariant(parent_task_id=parent_id, variant_type=kind,
                                        revision=index, variant_index=index,
                                        generation_config_json={})
            session.add(variant)
            await session.flush()
            attempt = GenerationAttempt(
                variant_id=variant.id, status=AttemptStatus.SUCCEEDED,
                submitted_at=datetime.now(UTC) - timedelta(minutes=3),
                completed_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(attempt)
            await session.flush()
            artifact = DownloadArtifact(
                attempt_id=attempt.id, status=DownloadStatus.READY,
                object_key=f"video-test/{kind.value}.mp4",
                duration_seconds=10, created_at=datetime.now(UTC),
            )
            session.add(artifact)
            await session.flush()
            artifacts.append(str(artifact.id))
        batch = MultiCutflowTask(
            user_id=ids["member"], idempotency_key="gallery-cut-a-b",
            payload_hash="b" * 64, status="COMPLETED",
            sources_json=[{"taskId": str(parent_id), "artifactId": a, "order": i + 1}
                          for i, a in enumerate(artifacts)],
            output_json=[{"id": "cut-final-1", "object_key": "cut-test/final.mp4",
                          "duration_seconds": 17.5,
                          "created_at": datetime.now(UTC).isoformat()}],
        )
        session.add(batch)
        await session.commit()
        batch_id = batch.id

    root = actor(ids, "root")
    result = await client.get(BASE, headers=root)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["taskId"] == str(parent_id)
    assert {c["artifactId"] for c in item["clips"]} == set(artifacts)
    assert item["cut"]["kind"] == "multi"
    assert item["cut"]["outputId"] == "cut-final-1"
    assert item["cut"]["durationSeconds"] == 17.5

    url = f"{BASE}/{parent_id}/cut/multi/{batch_id}/cut-final-1"
    monkeypatch.setattr(app.state.object_store, "localize", AsyncMock(return_value=video))
    playback = await client.get(f"{url}/play", headers=root)
    assert playback.status_code == 200
    assert playback.headers["content-type"].startswith("video/mp4")
    assert playback.content == video.read_bytes()
    assert "no-store" in playback.headers["cache-control"]
    assert (await client.get(f"{url}/play", headers=actor(ids, "member"))).status_code == 403
    assert (await client.get(f"{url}/play")).status_code == 401
    assert (await client.get(
        f"{BASE}/{uuid.uuid4()}/cut/multi/{batch_id}/cut-final-1/play", headers=root,
    )).status_code == 404
    assert (await client.get(
        f"{BASE}/{parent_id}/cut/multi/{batch_id}/missing/play", headers=root,
    )).status_code == 404

    from libtv_agent.api import admin as admin_routes
    monkeypatch.setattr(admin_routes, "media_command", AsyncMock(return_value=b"\xff\xd8\xff\xd9"))
    thumb = await client.get(f"{url}/thumbnail", headers=root)
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")


async def test_single_clip_cut_and_failed_cut_do_not_confuse_playable_state(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        parent = ParentTask(
            user_id=ids["member"], client_task_id="single-cut-gallery",
            title="单原片剪辑", asset_count=1, phase=ParentTaskPhase.DELIVERABLE,
        )
        session.add(parent)
        await session.flush()
        variant = GenerationVariant(
            parent_task_id=parent.id, variant_type=VariantKind.STABLE,
            variant_index=1, generation_config_json={},
        )
        session.add(variant)
        await session.flush()
        attempt = GenerationAttempt(
            variant_id=variant.id, status=AttemptStatus.SUCCEEDED,
            submitted_at=datetime.now(UTC) - timedelta(minutes=2),
            completed_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(attempt)
        await session.flush()
        artifact = DownloadArtifact(
            attempt_id=attempt.id, status=DownloadStatus.READY,
            object_key="test-single.mp4", created_at=datetime.now(UTC),
            duration_seconds=9,
        )
        session.add(artifact)
        await session.flush()
        handoff = CutflowHandoff(
            user_id=ids["member"], parent_task_id=parent.id,
            variant_id=variant.id, artifact_id=artifact.id,
            handoff_key="unique-test-gallery-handoff",
            status="COMPLETED",
            outputs_json=[{"id": "edited-1", "object_key": "edited-single.mp4",
                           "duration_seconds": 9.5, "created_at": datetime.now(UTC).isoformat()}],
        )
        session.add(handoff)
        await session.commit()
        handoff_id, parent_id = handoff.id, parent.id

    root = actor(ids, "root")
    listing = await client.get(BASE, headers=root)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    row = listing.json()["items"][0]
    assert row["cut"]["kind"] == "single"
    assert row["cut"]["batchId"] == str(handoff_id)
    assert len(row["clips"]) == 1

    async with app.state.database.session_factory() as session:
        handoff = await session.get(CutflowHandoff, handoff_id)
        handoff.outputs_json = []
        handoff.status = "FAILED"
        await session.commit()

    listing = await client.get(BASE, headers=root)
    row = listing.json()["items"][0]
    assert row["cut"] is None
    assert row["cutStatus"] == "FAILED"
    assert (await client.get(
        f"{BASE}/{parent_id}/cut/single/{handoff_id}/edited-1/play", headers=root,
    )).status_code == 404

async def test_gallery_includes_tasks_that_failed_before_any_video(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for suffix, phase, reason in (
            ("analysis", ParentTaskPhase.ACTION_REQUIRED, "AI_CREATIVE_WORKER_FAILED"),
            ("prepare", ParentTaskPhase.ACTION_REQUIRED, "MATERIALIZATION_INTERNAL_ERROR"),
            ("running", ParentTaskPhase.ANALYZING, None),
        ):
            session.add(ParentTask(
                user_id=ids["member"], client_task_id=f"early-{suffix}",
                title=f"early-{suffix}", asset_count=1, phase=phase,
                phase_reason_code=reason, created_at=datetime.now(UTC),
            ))
        await session.commit()
    root = actor(ids, "root")
    response = await client.get(BASE, headers=root, params={"search": "early-"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 3
    assert result["healthSummary"] == {"critical": 2, "warning": 0, "normal": 1}
    rows = {row["title"]: row for row in result["items"]}
    assert rows["early-analysis"]["phaseReasonCode"] == "AI_CREATIVE_WORKER_FAILED"
    assert rows["early-prepare"]["phaseReasonCode"] == "MATERIALIZATION_INTERNAL_ERROR"
    assert all(row["clips"] == [] and row["cut"] is None and row["latestRawAt"] is None for row in rows.values())
    filtered = await client.get(BASE, headers=root, params={"search": "early-", "health": "critical", "limit": 1})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert len(filtered.json()["items"]) == 1
    assert (await client.get(BASE, headers=actor(ids, "member"))).status_code == 403
