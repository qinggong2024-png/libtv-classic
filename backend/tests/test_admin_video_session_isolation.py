"""Regression: video inspection is a session-bound platform admin permission."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import User, UserSession
from test_account_access_budgets import account_app  # noqa: F401 (pytest fixture)


async def test_video_inspection_rejects_forged_headers_and_other_accounts(account_app):
    original, _, ids = account_app
    settings = original.state.settings.model_copy(update={
        "shared_authentication_enabled": True,
        "session_secret": "synthetic-video-inspection-test-session-secret-32-bytes",
    })
    tokens = {name: f"synthetic-video-inspection-cookie-{name}"
              for name in ("root", "member", "manager", "other")}
    async with original.state.database.session_factory() as session:
        other = await session.get(User, ids["other"])
        other.role = UserRole.ADMIN  # ADMIN without the protected username is not a manager.
        for name, token in tokens.items():
            session.add(UserSession(
                user_id=ids[name],
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ))
        await session.commit()

    app = create_app(settings)
    artifact_id, reference_id = uuid.uuid4(), uuid.uuid4()
    routes = (
        "/api/v1/admin/videos",
        f"/api/v1/admin/videos/{artifact_id}/play",
        f"/api/v1/admin/videos/{artifact_id}/thumbnail",
        f"/api/v1/admin/videos/{artifact_id}/trace",
        f"/api/v1/admin/videos/{artifact_id}/references/{reference_id}/content",
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in routes:
                response = await client.get(path, headers={"X-User-Id": str(ids["root"])})
                assert response.status_code == 401, (path, response.text)

            for name in ("member", "manager", "other"):
                client.cookies.set(settings.session_cookie_name, tokens[name])
                for path in routes:
                    response = await client.get(path, headers={"X-User-Id": str(ids["root"])})
                    assert response.status_code == 403, (name, path, response.text)

            client.cookies.set(settings.session_cookie_name, tokens["root"])
            listing = await client.get(routes[0], headers={"X-User-Id": str(ids["member"])})
            assert listing.status_code == 200, listing.text
            for path in routes[1:]:
                assert (await client.get(path)).status_code == 404

            async with original.state.database.session_factory() as session:
                root = await session.get(User, ids["root"])
                root.is_active = False
                await session.commit()
            assert (await client.get(routes[0])).status_code == 401
