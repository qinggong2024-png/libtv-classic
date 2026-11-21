import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import AuditEvent, User, UserSession
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor
from test_usage_reports import TODAY, entry

PATH = "/api/v1/admin/account-usage"


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr("libtv_agent.api.accounts.business_date", lambda: TODAY)


async def seed_usage(app, ids):
    async with app.state.database.session_factory() as session:
        session.add_all([
            entry(ids["member"], TODAY.isoformat(), 400_000, kind="CUTFLOW",
                  category="CUTFLOW", units=2),
            entry(ids["member"], TODAY.isoformat(), 600_000, kind="CUTFLOW",
                  category="CUTFLOW", units=3, status="RELEASED"),
            entry(ids["member"], TODAY.isoformat(), 1_200_000, kind="VIDEO",
                  category="VIDEO", units=6, status="RESERVED"),
            entry(ids["member"], TODAY.isoformat(), None, status="UNCERTAIN"),
            entry(ids["other"], TODAY.isoformat(), 900_000),
            AuditEvent(actor_id=ids["member"], action="PRIVATE_MEMBER_EVENT",
                       entity_type="user", entity_id=str(ids["member"]),
                       correlation_id="isolation-test"),
        ])
        await session.commit()


async def test_only_exact_management_admin_can_read_dashboard(account_app):
    app, client, ids = account_app
    await seed_usage(app, ids)
    async with app.state.database.session_factory() as session:
        other = await session.get(User, ids["other"])
        other.role = UserRole.ADMIN
        await session.commit()
    assert (await client.get(PATH)).status_code == 401
    for who in ("member", "manager", "other"):
        for query in ({}, {"userId": str(ids["member"])},
                      {"brandId": str(ids["a"]), "unpricedOnly": True},
                      {"kind": "CUTFLOW", "status": "RELEASED"}):
            response = await client.get(PATH, headers=actor(ids, who), params=query)
            assert response.status_code == 403
            assert "summaries" not in response.json()
            assert "PRIVATE_MEMBER_EVENT" not in response.text
    assert (await client.get(PATH, headers=actor(ids, "root"))).status_code == 200
    async with app.state.database.session_factory() as session:
        root = await session.get(User, ids["root"])
        root.role = UserRole.USER
        await session.commit()
    assert (await client.get(PATH, headers=actor(ids, "root"))).status_code == 403


async def test_personal_reports_reject_colleague_and_foreign_brand_filters(account_app):
    app, client, ids = account_app
    await seed_usage(app, ids)
    for who in ("member", "manager", "other"):
        for path in ("/api/v1/account/usage", "/api/v1/account/events"):
            for colleague in set(("member", "manager", "other", "root")) - {who}:
                assert (await client.get(path, headers=actor(ids, who), params={
                    "userId": str(ids[colleague]),
                })).status_code == 404
        data = (await client.get("/api/v1/account/usage", headers=actor(ids, who))).json()
        assert {row["userId"] for row in data["summaries"]} == {str(ids[who])}
        assert all(row["username"] == who for row in data["items"] + data["events"])


async def test_server_search_clip_totals_detail_filters_and_precision(account_app):
    app, client, ids = account_app
    await seed_usage(app, ids)
    params = {"includeEvents": False, "summaryLimit": 1}
    full = (await client.get(PATH, headers=actor(ids, "root"), params=params)).json()
    assert full["cutflowClips"] == 5  # Source clips include released usage records.
    assert full["budgetUsedUsd"] == 2.5
    assert full["releasedUsd"] == 0.6
    assert full["unpricedCount"] == 1
    for query, count in (({"kind": "CUTFLOW"}, 2), ({"unpricedOnly": True}, 1),
                         ({"kind": "CUTFLOW", "status": "RELEASED"}, 1)):
        data = (await client.get(PATH, headers=actor(ids, "root"), params={
            **params, **query,
        })).json()
        assert data["total"] == count
        assert data["budgetUsedUsd"] == full["budgetUsedUsd"]
        assert data["cutflowClips"] == 5
    member = (await client.get(PATH, headers=actor(ids, "root"), params={
        **params, "search": "MEMBER",
    })).json()
    assert member["summaryTotal"] == 1
    assert member["summaries"][0]["username"] == "member"
    assert member["summaries"][0]["cutflowClips"] == 5
    assert member["budgetUsedUsd"] == 1.6
    for search in ("%", "_", "no-such-account"):
        empty = (await client.get(PATH, headers=actor(ids, "root"), params={
            "search": search,
        })).json()
        assert empty["items"] == empty["summaries"] == []
        assert empty["summaryTotal"] == empty["budgetUsedUsd"] == 0
    for query in ({"kind": "UNKNOWN"}, {"search": "x" * 81}):
        assert (await client.get(PATH, headers=actor(ids, "root"), params=query)
                ).status_code == 422


async def test_http_sessions_ignore_spoofed_actor_headers_and_revoke_access(account_app):
    original, _, ids = account_app
    await seed_usage(original, ids)
    settings = original.state.settings.model_copy(update={
        "shared_authentication_enabled": True,
        "session_secret": "synthetic-usage-session-secret-at-least-thirty-two-characters",
    })
    app = create_app(settings)
    tokens = {name: "synthetic-usage-token-" + name
              for name in ("root", "member", "manager", "other")}
    async with original.state.database.session_factory() as session:
        other = await session.get(User, ids["other"])
        other.role = UserRole.ADMIN
        for name, token in tokens.items():
            session.add(UserSession(user_id=ids[name],
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await session.commit()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(PATH, headers=actor(ids, "root"))).status_code == 401
            for name, token in tokens.items():
                client.cookies.set(settings.session_cookie_name, token)
                response = await client.get(PATH, headers=actor(ids, "root"))
                assert response.status_code == (200 if name == "root" else 403)
                if name != "root":
                    own = (await client.get("/api/v1/account/usage",
                                            headers=actor(ids, "root"))).json()
                    assert {row["userId"] for row in own["summaries"]} == {str(ids[name])}
            client.cookies.set(settings.session_cookie_name, tokens["root"])
            async with app.state.database.session_factory() as session:
                root = await session.get(User, ids["root"])
                root.is_active = False
                await session.commit()
            assert (await client.get(PATH)).status_code == 401
