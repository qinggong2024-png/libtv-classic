import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from libtv_agent.domain.models import AccountUsage, AuditEvent, User
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

PATH = "/api/v1/account/usage"
TODAY = date(2026, 10, 19)


@pytest.fixture(autouse=True)
def fixed_business_day(monkeypatch):
    monkeypatch.setattr("libtv_agent.api.accounts.business_date", lambda: TODAY)


def entry(user_id, day, amount, *, status="ESTIMATED", kind="IMAGE", units=2, category="IMAGE"):
    return AccountUsage(
        user_id=user_id, operation_id=uuid.uuid4(), business_date=date.fromisoformat(day),
        amount_microusd=amount, status=status, kind=kind, units=units,
        pricing_category=category,
    )


async def test_range_summary_precision_zero_users_and_paging(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        other = await session.get(User, ids["other"])
        other.is_active = False
        session.add_all([
            entry(ids["member"], "2026-10-15", 100_001),
            entry(ids["member"], "2026-10-16", 200_002, status="RESERVED",
                  kind="VIDEO", category="VIDEO", units=6),
            entry(ids["member"], "2026-10-17", 300_003, status="UNCERTAIN"),
            entry(ids["member"], "2026-10-18", 999_999, status="RELEASED"),
            entry(ids["member"], "2026-10-18", None, kind="VIDEO", units=1, category=None),
            entry(ids["member"], "2026-10-18", None, status="RELEASED"),
            entry(ids["member"], "2026-10-14", 888_888),
            entry(ids["member"], "2026-10-20", 777_777),
            entry(ids["other"], "2026-10-16", 400_004),
        ])
        await session.commit()
    params = {"startDate": "2026-10-15", "endDate": "2026-10-18", "limit": 1}
    response = await client.get(PATH, headers=actor(ids, "root"), params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == data["recordCount"] == 7
    assert len(data["items"]) == 1
    assert data["startDate"] == "2026-10-15" and data["endDate"] == "2026-10-18"
    assert data["budgetDay"] == TODAY.isoformat()
    summaries = {row["username"]: row for row in data["summaries"]}
    assert set(summaries) == {"classic-admin", "manager", "member", "other"}
    assert summaries["manager"]["recordCount"] == 0
    assert summaries["manager"]["budgetUsedUsd"] == 0
    assert summaries["other"]["isActive"] is False
    member = summaries["member"]
    assert member["imageCount"] == 8
    assert member["videoSeconds"] == 6
    assert member["unknownVideoCount"] == 1
    assert member["unpricedCount"] == 1
    assert member["estimatedUsd"] == 0.100001
    assert member["reservedUsd"] == 0.200002
    assert member["uncertainUsd"] == 0.300003
    assert member["releasedUsd"] == 0.999999
    assert member["budgetUsedUsd"] == 0.600006
    assert data["budgetUsedUsd"] == 1.00001
    assert sum(Decimal(str(row["budgetUsedUsd"])) for row in data["summaries"]) == Decimal(
        str(data["budgetUsedUsd"])
    )
    second = (await client.get(
        PATH, headers=actor(ids, "root"), params={**params, "offset": 1}
    )).json()
    assert second["summaries"] == data["summaries"]
    assert second["budgetUsedUsd"] == data["budgetUsedUsd"]
    assert second["items"][0]["id"] != data["items"][0]["id"]
    occupied = (await client.get(
        PATH, headers=actor(ids, "root"), params={**params, "status": "OCCUPIED"}
    )).json()
    assert occupied["total"] == 5
    assert occupied["summaries"] == data["summaries"]
    assert all(item["status"] != "RELEASED" for item in occupied["items"])
    released = (await client.get(
        PATH, headers=actor(ids, "root"),
        params={**params, "status": "RELEASED", "userId": str(ids["member"])},
    )).json()
    assert released["total"] == 2
    assert released["recordCount"] == 6
    assert len(released["summaries"]) == 1


async def test_today_budget_is_independent_of_historical_range(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for name, budget in (("member", 1_000_000), ("manager", 0), ("other", None)):
            user = await session.get(User, ids[name])
            user.daily_budget_microusd = budget
        session.add_all([
            entry(ids["member"], "2026-10-15", 9_000_000),
            entry(ids["member"], TODAY.isoformat(), 123_456, status="RESERVED"),
            entry(ids["member"], TODAY.isoformat(), 5_000_000, status="RELEASED"),
        ])
        await session.commit()
    data = (await client.get(PATH, headers=actor(ids, "root"), params={
        "day": "2026-10-15",
    })).json()
    summaries = {row["username"]: row for row in data["summaries"]}
    member = summaries["member"]
    assert member["budgetUsedUsd"] == 9
    assert member["todayBudgetUsedUsd"] == 0.123456
    assert member["todayRemainingUsd"] == 0.876544
    assert summaries["manager"]["todayRemainingUsd"] == 0
    assert summaries["other"]["todayRemainingUsd"] is None
    async with app.state.database.session_factory() as session:
        session.add(entry(ids["member"], TODAY.isoformat(), None, status="UNCERTAIN"))
        await session.commit()
    unknown = (await client.get(PATH, headers=actor(ids, "member"))).json()["summaries"][0]
    assert unknown["todayRemainingUsd"] is None
    assert unknown["todayUnpricedCount"] == 1
    assert unknown["unpricedCount"] == 1


async def test_summary_scope_and_foreign_filters(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        session.add_all([
            entry(ids["member"], TODAY.isoformat(), 100_000),
            entry(ids["other"], TODAY.isoformat(), 900_000),
        ])
        await session.commit()
    member = (await client.get(PATH, headers=actor(ids, "member"))).json()
    assert [row["username"] for row in member["summaries"]] == ["member"]
    manager = (await client.get(PATH, headers=actor(ids, "manager"))).json()
    assert {row["username"] for row in manager["summaries"]} == {"manager"}
    assert manager["budgetUsedUsd"] == 0
    for name in ("manager", "member"):
        for filters in ({"userId": str(ids["other"])}, {"brandId": str(ids["b"])}):
            assert (await client.get(
                PATH, headers=actor(ids, name), params=filters
            )).status_code == 404
    brand = (await client.get(
        PATH, headers=actor(ids, "root"), params={"brandId": str(ids["b"])}
    )).json()
    assert [row["username"] for row in brand["summaries"]] == ["other"]
    assert brand["budgetUsedUsd"] == 0.9


async def test_events_use_shanghai_boundaries_and_stay_recent_on_detail_paging(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for hour, day in ((15, 14), (16, 14), (15, 15), (16, 15)):
            session.add(AuditEvent(
                actor_id=ids["member"], action=f"DAY{day}H{hour}",
                entity_type="user", entity_id=str(ids["member"]), correlation_id="usage-test",
                created_at=datetime(2026, 10, day, hour, 0, tzinfo=UTC),
            ))
        session.add(AuditEvent(
            actor_id=ids["other"], action="FOREIGN", entity_type="user",
            entity_id=str(ids["other"]), correlation_id="usage-test",
            created_at=datetime(2026, 10, 15, 0, 0, tzinfo=UTC),
        ))
        await session.commit()
    params = {"day": "2026-10-15", "offset": 50}
    data = (await client.get(PATH, headers=actor(ids, "member"), params=params)).json()
    assert {event["action"] for event in data["events"]} == {"DAY14H16", "DAY15H15"}
    assert data["items"] == []
    assert data["startDate"] == data["endDate"] == "2026-10-15"


async def test_usage_events_have_independent_backend_pagination(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for index in range(65):
            session.add(AuditEvent(
                actor_id=ids["member"], action="PAGINATION_TEST", entity_type="user",
                entity_id=str(ids["member"]), correlation_id="usage-event-pagination",
                created_at=datetime(2026, 10, 19, 1, 0, tzinfo=UTC)
                + timedelta(minutes=index),
            ))
        await session.commit()
    pages = [(await client.get(PATH, headers=actor(ids, "member"), params={
        "day": TODAY.isoformat(), "eventLimit": 20, "eventOffset": offset,
    })).json() for offset in (0, 20, 40, 60)]
    assert [len(page["events"]) for page in pages] == [20, 20, 20, 5]
    assert all(page["eventTotal"] == 65 for page in pages)
    items = [event for page in pages for event in page["events"]]
    assert len({event["id"] for event in items}) == 65
    assert {event["action"] for event in items} == {"PAGINATION_TEST"}
    assert items == sorted(items, key=lambda event: event["createdAt"], reverse=True)
    default_page = (await client.get(PATH, headers=actor(ids, "member"), params={
        "day": TODAY.isoformat(),
    })).json()
    assert len(default_page["events"]) == 50
    assert default_page["eventTotal"] == 65


async def test_summary_pages_keep_full_scope_totals_and_detail_names(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        session.add_all([
            entry(ids["member"], TODAY.isoformat(), 100_001),
            entry(ids["other"], TODAY.isoformat(), 200_002),
        ])
        await session.commit()
    params = {"summaryLimit": 1, "includeEvents": False}
    pages = [(await client.get(
        PATH, headers=actor(ids, "root"), params={**params, "summaryOffset": index}
    )).json() for index in range(4)]
    assert all(page["summaryTotal"] == 4 for page in pages)
    assert all(len(page["summaries"]) == 1 for page in pages)
    assert len({page["summaries"][0]["userId"] for page in pages}) == 4
    assert all(page["budgetUsedUsd"] == 0.300003 for page in pages)
    assert all(page["recordCount"] == 2 for page in pages)
    assert all({item["username"] for item in page["items"]} == {"member", "other"}
               for page in pages)
    assert all(page["events"] == [] for page in pages)
    assert all(page["eventTotal"] == 0 for page in pages)
    empty = (await client.get(
        PATH, headers=actor(ids, "root"), params={**params, "summaryOffset": 100}
    )).json()
    assert empty["summaries"] == []
    assert empty["budgetUsedUsd"] == 0.300003
    own = (await client.get(PATH, headers=actor(ids, "member"), params=params)).json()
    assert own["summaryTotal"] == 1
    assert own["budgetUsedUsd"] == 0.100001


async def test_events_paging_filters_scope_and_shanghai_dates(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        for index in range(65):
            session.add(AuditEvent(
                actor_id=ids["member"], action="LOGIN" if index % 2 else "CHANGE_PASSWORD",
                entity_type="user", entity_id=str(ids["member"]), correlation_id="paging-test",
                created_at=datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
                + timedelta(minutes=index),
            ))
        session.add_all([
            AuditEvent(
                actor_id=ids["other"], action="LOGIN", entity_type="user",
                entity_id=str(ids["other"]), correlation_id="foreign-test",
                created_at=datetime(2026, 10, 19, 1, 0, tzinfo=UTC),
            ),
            AuditEvent(
                actor_id=ids["member"], action="LOGIN", entity_type="user",
                entity_id=str(ids["member"]), correlation_id="boundary-test",
                created_at=datetime(2026, 10, 19, 16, 0, tzinfo=UTC),
            ),
        ])
        await session.commit()
    path = "/api/v1/account/events"
    pages = [(await client.get(path, headers=actor(ids, "root"), params={
        "startDate": TODAY.isoformat(), "endDate": TODAY.isoformat(),
        "limit": 20, "offset": offset, "userId": str(ids["member"]),
    })).json() for offset in (0, 20, 40, 60)]
    assert [len(page["items"]) for page in pages] == [20, 20, 20, 5]
    assert all(page["total"] == 65 for page in pages)
    items = [item for page in pages for item in page["items"]]
    assert len({item["id"] for item in items}) == 65
    assert {item["username"] for item in items} == {"member"}
    assert items == sorted(items, key=lambda item: item["createdAt"], reverse=True)
    assert all(item["entityId"] == str(ids["member"]) for item in items)
    own = (await client.get(path, headers=actor(ids, "manager"))).json()
    assert own["total"] == 0 and own["items"] == []
    filtered = (await client.get(path, headers=actor(ids, "root"), params={
        "userId": str(ids["member"]), "action": "LOGIN", "limit": 100,
    })).json()
    assert filtered["total"] == len(filtered["items"]) == 32
    assert all(item["action"] == "LOGIN" for item in filtered["items"])
    brand = (await client.get(path, headers=actor(ids, "root"), params={
        "brandId": str(ids["b"]),
    })).json()
    assert brand["total"] == 1 and brand["items"][0]["username"] == "other"
    for who in ("manager", "member"):
        for params in ({"userId": str(ids["other"])}, {"brandId": str(ids["b"])}):
            assert (await client.get(path, headers=actor(ids, who), params=params)
                    ).status_code == 404
    for params in ({"offset": -1}, {"limit": 101}, {"startDate": TODAY.isoformat()},
                   {"startDate": "2026-10-20", "endDate": "2026-10-19"},
                   {"startDate": "0001-01-01", "endDate": "9999-12-31"}):
        assert (await client.get(path, headers=actor(ids, "root"), params=params)
                ).status_code == 422


@pytest.mark.parametrize("params", [
    {"startDate": "2026-10-15"},
    {"endDate": "2026-10-15"},
    {"startDate": "2026-10-19", "endDate": "2026-10-15"},
    {"day": "2026-10-15", "startDate": "2026-10-15", "endDate": "2026-10-19"},
    {"day": "invalid"},
    {"day": "9999-12-31"},
    {"day": "0001-01-01"},
    {"status": "UNKNOWN"},
    {"offset": -1},
    {"eventOffset": -1},
    {"eventLimit": 101},
])
async def test_invalid_filters_are_rejected(account_app, params):
    _, client, ids = account_app
    response = await client.get(PATH, headers=actor(ids, "root"), params=params)
    assert response.status_code == 422
