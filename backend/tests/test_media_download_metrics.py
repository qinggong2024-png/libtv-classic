"""Unique first-download counts are separate from generation and playback usage."""

import uuid
from datetime import UTC, datetime

from libtv_agent.domain.models import AuditEvent
from libtv_agent.services.media_download_metrics import CUTFLOW_DOWNLOAD, ORIGINAL_DOWNLOAD
from test_account_access_budgets import account_app, actor


def event(user_id, action, media_id, at):
    return AuditEvent(
        actor_id=user_id,
        action=action,
        entity_type="video_download",
        entity_id=media_id,
        correlation_id=str(uuid.uuid4()),
        created_at=datetime.fromisoformat(at).replace(tzinfo=UTC),
    )


async def test_admin_usage_counts_unique_downloads_on_first_date(account_app):
    app, client, ids = account_app
    original = "artifact-one"
    cut = "handoff-one/render-one"
    async with app.state.database.session_factory() as session:
        session.add_all([
            event(ids["member"], ORIGINAL_DOWNLOAD, original, "2026-10-17T02:00:00"),
            event(ids["member"], ORIGINAL_DOWNLOAD, original, "2026-10-17T06:00:00"),
            event(ids["member"], ORIGINAL_DOWNLOAD, original, "2026-10-18T02:00:00"),
            event(ids["member"], ORIGINAL_DOWNLOAD, "artifact-two", "2026-10-18T04:00:00"),
            event(ids["member"], CUTFLOW_DOWNLOAD, cut, "2026-10-17T03:00:00"),
            event(ids["member"], CUTFLOW_DOWNLOAD, cut, "2026-10-18T03:00:00"),
            event(ids["other"], ORIGINAL_DOWNLOAD, original, "2026-10-18T03:00:00"),
            # Non-download events must not inflate the counters.
            AuditEvent(
                actor_id=ids["member"],
                action="VIEW_VIDEO",
                entity_type="video_download",
                entity_id="artifact-three",
                correlation_id=str(uuid.uuid4()),
                created_at=datetime(2026, 10, 17, 3, tzinfo=UTC),
            ),
        ])
        await session.commit()

    url = "/api/v1/admin/account-usage"
    day17 = (await client.get(url, headers=actor(ids, "root"), params={
        "startDate": "2026-10-17", "endDate": "2026-10-17", "includeEvents": False,
    })).json()
    summary17 = {item["username"]: item for item in day17["summaries"]}
    assert (summary17["member"]["originalDownloads"], summary17["member"]["cutflowDownloads"]) == (1, 1)
    assert (day17["originalDownloads"], day17["cutflowDownloads"]) == (1, 1)
    assert (summary17["other"]["originalDownloads"], summary17["other"]["cutflowDownloads"]) == (0, 0)

    day18 = (await client.get(url, headers=actor(ids, "root"), params={
        "startDate": "2026-10-18", "endDate": "2026-10-18", "includeEvents": False,
    })).json()
    summary18 = {item["username"]: item for item in day18["summaries"]}
    assert (summary18["member"]["originalDownloads"], summary18["member"]["cutflowDownloads"]) == (1, 0)
    assert (summary18["other"]["originalDownloads"], summary18["other"]["cutflowDownloads"]) == (1, 0)
    assert (day18["originalDownloads"], day18["cutflowDownloads"]) == (2, 0)

    across = (await client.get(url, headers=actor(ids, "root"), params={
        "startDate": "2026-10-17", "endDate": "2026-10-18",
        "search": "member", "includeEvents": False,
        "summaryLimit": 1,
    })).json()
    assert (across["originalDownloads"], across["cutflowDownloads"]) == (2, 1)
    assert len(across["summaries"]) == 1
    assert (across["summaries"][0]["originalDownloads"], across["summaries"][0]["cutflowDownloads"]) == (2, 1)

    personal = (await client.get("/api/v1/account/usage", headers=actor(ids, "member"), params={
        "startDate": "2026-10-17", "endDate": "2026-10-18",
    })).json()
    assert (personal["originalDownloads"], personal["cutflowDownloads"]) == (2, 1)
    assert {item["username"] for item in personal["summaries"]} == {"member"}
