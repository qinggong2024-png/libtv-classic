"""Only exact management admins may review other users' saved generation videos."""
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from libtv_agent.domain.enums import AttemptStatus, DownloadStatus, ParentTaskPhase, VariantKind
from libtv_agent.domain.models import (
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
    User,
)
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

BASE = "/api/v1/admin/videos"


async def test_admin_recent_video_listing_and_cross_owner_media_are_read_only(
    account_app, tmp_path, monkeypatch
):
    app, client, ids = account_app
    saved = tmp_path / "verified-test-video.mp4"
    saved.write_bytes(b"synthetic-media-file-for-tests-only")
    art_ids = []
    async with app.state.database.session_factory() as session:
        for i, user_key in enumerate(("member", "other")):
            parent = ParentTask(
                user_id=ids[user_key], client_task_id=f"review-{i}",
                title=f"quality-{i}", asset_count=1, phase=ParentTaskPhase.DELIVERABLE,
            )
            session.add(parent)
            await session.flush()
            variant = GenerationVariant(
                parent_task_id=parent.id, variant_type=VariantKind.STABLE,
                generation_config_json={},
            )
            session.add(variant)
            await session.flush()
            submitted = datetime.now(UTC) - timedelta(days=i, minutes=2)
            attempt = GenerationAttempt(
                variant_id=variant.id,
                status=AttemptStatus.SUCCEEDED,
                submitted_at=submitted,
                completed_at=submitted + timedelta(seconds=90),
            )
            session.add(attempt)
            await session.flush()
            artifact = DownloadArtifact(
                attempt_id=attempt.id,
                status=DownloadStatus.READY,
                object_key=f"videos/quality-{i}.mp4",
                created_at=datetime.now(UTC) - timedelta(days=i),
                duration_seconds=9.0,
            )
            session.add(artifact)
            await session.flush()
            art_ids.append((parent.id, artifact.id))
        session.add(MultiCutflowTask(
            user_id=ids["other"],
            idempotency_key="quality-review-failure-test",
            payload_hash="a" * 64,
            sources_json=[{"artifactId": str(art_ids[1][1])}],
            status="FAILED",
        ))
        await session.commit()

    monkeypatch.setattr(app.state.object_store, "localize", AsyncMock(return_value=saved))
    root = actor(ids, "root")
    listing = await client.get(BASE, headers=root, params={"days": 7})
    assert listing.status_code == 200, listing.text
    result = listing.json()
    assert result["total"] == 2
    assert {r["username"] for r in result["items"]} == {"member", "other"}
    assert {r["brandName"] for r in result["items"]} == {"品牌 A", "品牌 B"}
    assert all("object_key" not in r for r in result["items"])
    assert result["healthSummary"] == {"critical": 1, "warning": 0, "normal": 1}
    assert result["items"][0]["health"]["primary"]["code"] == "CUT_FAILED"
    assert result["items"][0]["username"] == "other"
    assert result["items"][0]["health"]["primary"]["stage"] == "CUT 送剪"
    flagged = await client.get(BASE, headers=root, params={"health": "critical"})
    assert flagged.status_code == 200
    assert flagged.json()["total"] == 1
    assert flagged.json()["items"][0]["health"]["severity"] == "critical"
    normal = await client.get(BASE, headers=root, params={"health": "normal"})
    assert normal.json()["total"] == 1
    newest = await client.get(BASE, headers=root, params={"sort": "newest"})
    assert newest.json()["items"][0]["title"] == "quality-0"

    narrow = await client.get(BASE, headers=root, params={"days": 7, "search": "quality-0"})
    assert narrow.json()["total"] == 1
    brand = await client.get(BASE, headers=root, params={"brand_id": str(ids["b"])})
    assert brand.json()["total"] == 1 and brand.json()["items"][0]["username"] == "other"
    page = await client.get(BASE, headers=root, params={"limit": 1, "offset": 1})
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    one_day = await client.get(BASE, headers=root, params={"days": 1})
    assert one_day.json()["total"] == 1

    media_id = art_ids[0][1]
    play = await client.get(f"{BASE}/{media_id}/play", headers=root)
    assert play.status_code == 200
    assert play.headers["content-type"].startswith("video/mp4")
    assert play.content == saved.read_bytes()
    assert "no-store" in play.headers["cache-control"]
    from libtv_agent.api import admin as admin_routes
    monkeypatch.setattr(admin_routes, "media_command",
                        AsyncMock(return_value=b"\\xff\\xd8\\xff\\xd9"))
    thumbnail = await client.get(f"{BASE}/{media_id}/thumbnail", headers=root)
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/jpeg")
    bad_id = await client.get(f"{BASE}/{uuid.uuid4()}/play", headers=root)
    assert bad_id.status_code == 404

    # Normal download permissions remain owner-only.
    assert (await client.get(
        f"/api/v1/downloads/{media_id}", headers=actor(ids, "other")
    )).status_code == 404


async def test_other_accounts_cannot_enumerate_or_play_admin_videos(account_app):
    app, client, ids = account_app
    assert (await client.get(BASE)).status_code == 401
    assert (await client.get(f"{BASE}/{uuid.uuid4()}/play")).status_code == 401
    for who in ("member", "manager", "other"):
        for path in (BASE, f"{BASE}/{uuid.uuid4()}/play",
                     f"{BASE}/{uuid.uuid4()}/thumbnail"):
            response = await client.get(path, headers=actor(ids, who))
            assert response.status_code == 403, response.text
    for params in ({"days": 100}, {"limit": 100}, {"search": "x" * 81},
                   {"health": "invalid"}, {"sort": "invalid"}):
        response = await client.get(BASE, headers=actor(ids, "root"), params=params)
        assert response.status_code == 422
    async with app.state.database.session_factory() as session:
        root = await session.get(User, ids["root"])
        root.is_active = False
        await session.commit()
    assert (await client.get(BASE, headers=actor(ids, "root"))).status_code == 403
