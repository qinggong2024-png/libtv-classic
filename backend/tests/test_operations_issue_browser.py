"""Cross-account operations diagnostics remain read-only and platform-admin-only."""
from datetime import UTC, datetime

from libtv_agent.domain.enums import AttemptStatus, ParentTaskPhase, ReservationStatus, VariantKind
from libtv_agent.domain.models import (
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    PointsReservation,
    User,
)
from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor

BASE = "/api/v1/admin/operations/issues"


async def _add_attempt(session, user_id, index, status, phase, reason=None, reserve=False):
    parent = ParentTask(
        user_id=user_id, client_task_id=f"ops-{index}",
        title=f"issue-{index}", asset_count=1, phase=phase, phase_reason_code=reason,
    )
    session.add(parent)
    await session.flush()
    variant = GenerationVariant(
        parent_task_id=parent.id,
        variant_type=VariantKind.STABLE,
        generation_config_json={},
    )
    session.add(variant)
    await session.flush()
    attempt = GenerationAttempt(
        variant_id=variant.id, status=status,
        provider="kling", last_error_code=f"ERROR_{index}",
        error_message="Provider needs manual verification.",
        task_id=f"kling-cloud-{index}",
    )
    session.add(attempt)
    await session.flush()
    if reserve:
        session.add(PointsReservation(
            user_id=user_id, attempt_id=attempt.id,
            estimated_points=1, business_date=datetime.now(UTC).date(),
            status=ReservationStatus.UNCERTAIN,
        ))
    return parent


async def test_platform_admin_browses_other_accounts_without_leaking_owner_tasks(account_app):
    app, client, ids = account_app
    async with app.state.database.session_factory() as session:
        cross_task = await _add_attempt(
            session, ids["member"], 1,
            AttemptStatus.UNKNOWN_AFTER_SUBMIT, ParentTaskPhase.ACTION_REQUIRED, reserve=True,
        )
        other_task = await _add_attempt(
            session, ids["other"], 2,
            AttemptStatus.UNKNOWN_AFTER_SUBMIT, ParentTaskPhase.ACTION_REQUIRED, reserve=True,
        )
        await _add_attempt(
            session, ids["other"], 3,
            AttemptStatus.PREPARED, ParentTaskPhase.ACTION_REQUIRED,
            reason="USD_DAILY_BUDGET_EXCEEDED",
        )
        await _add_attempt(
            session, ids["member"], 4,
            AttemptStatus.PREPARED, ParentTaskPhase.QUEUED,
        )
        await _add_attempt(
            session, ids["member"], 5,
            AttemptStatus.SUCCEEDED, ParentTaskPhase.DELIVERABLE, reserve=True,
        )
        await session.commit()
        cross_id, other_id = str(cross_task.id), str(other_task.id)
    root = actor(ids, "root")

    cloud = (await client.get(BASE, headers=root, params={"category": "cloudUnknown"}))
    assert cloud.status_code == 200, cloud.text
    assert cloud.json()["total"] == 2
    assert {i["taskId"] for i in cloud.json()["items"]} == {cross_id, other_id}
    assert {i["username"] for i in cloud.json()["items"]} == {"member", "other"}
    assert {i["brandName"] for i in cloud.json()["items"]} == {"品牌 A", "品牌 B"}
    assert all(i["attemptId"] and i["providerTaskId"] for i in cloud.json()["items"])
    assert "password_hash" not in cloud.text
    assert "raw_response_json" not in cloud.text

    reserved = await client.get(BASE, headers=root, params={"category": "reservationReview"})
    assert reserved.status_code == 200
    assert reserved.json()["total"] == 3
    assert all(item["reservationId"] for item in reserved.json()["items"])

    blocked = await client.get(BASE, headers=root, params={"category": "blockedPrepared"})
    assert blocked.status_code == 200
    assert blocked.json()["total"] == 1
    assert blocked.json()["items"][0]["phaseReasonCode"] == "USD_DAILY_BUDGET_EXCEEDED"

    page = await client.get(BASE, headers=root, params={
        "category": "cloudUnknown", "limit": 1, "offset": 1,
    })
    assert page.status_code == 200
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    search = await client.get(BASE, headers=root, params={
        "category": "cloudUnknown", "search": "品牌 B",
    })
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["taskId"] == other_id
    search_none = await client.get(BASE, headers=root, params={
        "category": "cloudUnknown", "search": "nonexistent",
    })
    assert search_none.json()["total"] == 0

    # New read-only admin endpoint must not weaken ordinary owner-only routes.
    other_response = await client.get(f"/api/v1/tasks/{cross_id}", headers=actor(ids, "other"))
    member_response = await client.get(f"/api/v1/tasks/{other_id}", headers=actor(ids, "member"))
    assert other_response.status_code == 404
    assert member_response.status_code == 404


async def test_operations_issues_permission_validation_and_revocation(account_app):
    app, client, ids = account_app
    assert (await client.get(BASE, params={"category": "cloudUnknown"})).status_code == 401
    for who in ("member", "manager", "other"):
        for category in ("cloudUnknown", "reservationReview", "blockedPrepared"):
            res = await client.get(BASE, headers=actor(ids, who), params={"category": category})
            assert res.status_code == 403
            assert "items" not in res.text
    for params in (
        {"category": "INVALID"},
        {"category": "cloudUnknown", "limit": 101},
        {"category": "cloudUnknown", "offset": -1},
        {"category": "cloudUnknown", "search": "x" * 81},
    ):
        response = await client.get(BASE, headers=actor(ids, "root"), params=params)
        assert response.status_code == 422
    async with app.state.database.session_factory() as session:
        root = await session.get(User, ids["root"])
        root.is_active = False
        await session.commit()
    assert (await client.get(BASE, headers=actor(ids, "root"),
                             params={"category": "cloudUnknown"})).status_code == 403
