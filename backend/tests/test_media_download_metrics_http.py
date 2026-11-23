"""HTTP download events: preview and inaccessible files must never count."""

from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.models import AuditEvent
from libtv_agent.services.media_download_metrics import ORIGINAL_DOWNLOAD
from sqlalchemy import select

from test_cutflow_handoff import handoff_env as handoff_env


async def test_original_download_only_explicit_owner_downloads_are_logged(handoff_env):
    database, settings, store, ids = handoff_env
    app = create_app(settings)
    app.state.database = database
    app.state.object_store = store
    url = f"/api/v1/downloads/{ids['artifact']}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner = {"X-User-Id": str(ids["user"])}
        other = {"X-User-Id": str(ids["other"])}
        assert (await client.get(url, headers=owner)).status_code == 200  # Preview
        assert (await client.get(url + "?download=true", headers=other)).status_code == 404
        assert (await client.get(url + "?download=true")).status_code == 401
        for _ in range(2):
            result = await client.get(url + "?download=true", headers=owner)
            assert result.status_code == 200

    async with database.session_factory() as session:
        events = list(await session.scalars(select(AuditEvent).where(
            AuditEvent.action == ORIGINAL_DOWNLOAD,
        )))
    assert len(events) == 2  # Log each explicit request; unique report counts once.
    assert {item.actor_id for item in events} == {ids["user"]}
    assert {item.entity_id for item in events} == {str(ids["artifact"])}
