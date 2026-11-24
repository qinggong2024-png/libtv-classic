import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from libtv_agent.domain.models import (
    AuditEvent, ImageGenerationBatch, MultiCutflowTask, ParentTask, AccountUsage,
)
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor


@pytest.mark.asyncio
async def test_production_feed_quantities_scope_paging_and_legacy_audit(account_app):
    app, client, ids = account_app
    at = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
    async with app.state.database.session_factory() as session:
        video = ParentTask(
            id=uuid.uuid4(), user_id=ids["member"], client_task_id="video-1",
            title="test video", asset_count=1, settings_json={"resultCount": 2},
            created_at=at,
        )
        old = ParentTask(
            user_id=ids["member"], client_task_id="legacy",
            title="legacy", asset_count=1, settings_json={}, created_at=at,
        )
        foreign = ParentTask(
            user_id=ids["other"], client_task_id="foreign",
            title="foreign", asset_count=1, settings_json={"resultCount": 99}, created_at=at,
        )
        batch = ImageGenerationBatch(
            user_id=ids["member"], client_batch_id="image-1", idempotency_key="image-key",
            request_hash="a" * 64, name="test image", provider="test",
            expected_generation_count=4, paid_submission_consent_at=at,
            request_json={}, created_at=at + timedelta(minutes=1),
        )
        cut = MultiCutflowTask(
            user_id=ids["member"], idempotency_key="cut-key", payload_hash="b" * 64,
            sources_json=[{"id": "a"}, {"id": "b"}], created_at=at + timedelta(minutes=2),
        )
        session.add_all([video, old, foreign, batch, cut])
        def log(action, entity, when=at, kind="parent_task", state=None):
            return AuditEvent(
                actor_id=ids["member"], action=action, entity_type=kind,
                entity_id=entity, correlation_id=uuid.uuid4().hex, created_at=when,
                to_state=state,
            )
        session.add_all([
            log("CREATE_FIXED_VIDEO_TASK", str(video.id)),
            log("QUEUE_FIXED_VIDEO_TASK", str(video.id)),
            log("AUTO_CONFIRM_TASK", str(video.id)),
            log("ANALYSIS_RETRY_STARTED", str(video.id)),
            log("LOGIN", str(ids["member"]), kind="user"),
            log("DOWNLOAD_ORIGINAL_VIDEO", "original", at + timedelta(minutes=3), "video_download"),
            log("DOWNLOAD_ORIGINAL_VIDEO", "original", at + timedelta(minutes=4), "video_download"),
            log("DOWNLOAD_CUTFLOW_VIDEO", "final", at + timedelta(minutes=5), "video_download"),
            log("DOWNLOAD_CUTFLOW_VIDEO", "old-final", at - timedelta(days=1), "video_download"),
            log("DOWNLOAD_CUTFLOW_VIDEO", "old-final", at + timedelta(minutes=6), "video_download"),
            log("FINAL_VIDEO_APPROVAL", "variant", at + timedelta(minutes=7), "generation_variant", "APPROVED"),
            AccountUsage(user_id=ids["member"], kind="VIDEO", operation_id=uuid.uuid4(),
                         business_date=date(2026, 10, 19), units=20, status="RELEASED",
                         amount_microusd=0, created_at=at),
        ])
        await session.commit()
    path = "/api/v1/account/events"
    params = {"view": "production", "startDate": "2026-10-19", "endDate": "2026-10-19",
              "userId": str(ids["member"])}
    response = await client.get(path, headers=actor(ids, "root"), params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["view"] == "production" and data["total"] == 8
    rows = data["items"]
    assert {r["username"] for r in rows} == {"member"}
    assert rows == sorted(rows, key=lambda r: r["createdAt"], reverse=True)
    videos = [r for r in rows if r["action"] == "CREATE_VIDEO_TASK"]
    assert len(videos) == 2
    assert {r["detailTitle"]: r["quantity"] for r in videos} == {"test video": 2, "legacy": None}
    assert next(r for r in rows if r["action"] == "CREATE_IMAGE_TASK")["quantity"] == 4
    assert next(r for r in rows if r["action"] == "CREATE_CUT_TASK")["quantity"] == 2
    assert len([r for r in rows if r["category"] == "DOWNLOAD"]) == 2
    assert next(r for r in rows if r["action"] == "FINAL_VIDEO_APPROVAL")["outcome"] == "APPROVED"
    assert next(r for r in rows if r["action"] == "LOGIN")["detailTitle"] is None
    for category, count in [("VIDEO", 2), ("IMAGE", 1), ("CUTFLOW", 1), ("DOWNLOAD", 2), ("REVIEW", 1), ("SYSTEM", 1)]:
        selected = (await client.get(path, headers=actor(ids, "root"),
                    params={**params, "category": category})).json()
        assert selected["total"] == count
        assert all(r["category"] == category for r in selected["items"])
    paged = [(await client.get(path, headers=actor(ids, "root"),
              params={**params, "offset": offset, "limit": 3})).json()
             for offset in (0, 3, 6)]
    assert [len(p["items"]) for p in paged] == [3, 3, 2]
    assert len({r["id"] for p in paged for r in p["items"]}) == 8
    assert all(p["total"] == 8 for p in paged)
    assert (await client.get(path, headers=actor(ids, "member"),
            params={**params, "userId": str(ids["other"])})).status_code == 404
    legacy = (await client.get(path, headers=actor(ids, "root"),
              params={**params, "view": "audit", "action": "QUEUE_FIXED_VIDEO_TASK"})).json()
    assert legacy["total"] == 1 and legacy["items"][0]["action"] == "QUEUE_FIXED_VIDEO_TASK"
