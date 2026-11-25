from test_account_access_budgets import account_app as account_app
from test_account_access_budgets import actor


async def test_performance_snapshot_requires_management_admin(account_app):
    _, client, ids = account_app
    path = "/api/v1/admin/performance"
    assert (await client.get(path)).status_code == 401
    for who in ("member", "manager", "other"):
        assert (await client.get(path, headers=actor(ids, who))).status_code == 403
    result = await client.get(path, headers=actor(ids, "root"))
    assert result.status_code == 200
    assert result.json()["scope"] == "current-api-process"
    assert "rows" in result.json()
    assert "activeTransactions" in result.json()
