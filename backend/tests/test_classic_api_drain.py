from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from test_analysis_lifecycle import _create_database, _settings

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from classic_api_drain import assess  # noqa: E402
from classic_api_slot_plan import make_plan  # noqa: E402


def sample(port):
    return {
        "contractVersion": 1,
        "deploymentId": "libtv-classic-lan-0e3d1c8",
        "port": port,
        "sourceRevision": "a" * 64,
        "ownsRecovery": False,
        "retirementReady": True,
        "workCounts": dict.fromkeys(
            (
                "analysis_jobs",
                "confirmation_jobs",
                "subject_element_jobs",
                "feedback_learning_jobs",
                "http",
                "recovery",
                "imageRefresh",
                "login",
            ),
            0,
        ),
    }


@pytest.mark.parametrize("active,standby", [(8002, 8003), (8003, 8002)])
def test_symmetric_drain_and_plan_fail_closed(active, standby):
    route = {
        "activeApiPort": active,
        "hotRouteVersion": 1,
        "configured": True,
        "allowedApiPorts": [8002, 8003],
        "drainContractVersion": 1,
        "pendingRouteSelections": 0,
        "inFlightByPort": {str(standby): 0},
    }
    process = sample(standby)
    assert assess(standby, route, process, expected_revision="a" * 64)["ready"]
    assert not assess(active, route, sample(active), expected_revision="a" * 64)["ready"]
    process["workCounts"]["confirmation_jobs"] = 1
    assert not assess(standby, route, process, expected_revision="a" * 64)["ready"]
    process["workCounts"]["confirmation_jobs"] = 0
    route["pendingRouteSelections"] = 1
    assert not assess(standby, route, process, expected_revision="a" * 64)["ready"]
    route["pendingRouteSelections"] = 0
    info = {"sourceRevision": "a" * 64, "schemaRevision": "schema", "configRevision": "cfg"}
    gate = {
        "configured": True,
        "deploymentId": "libtv-classic-lan-0e3d1c8",
        "newVideoAdmissionsPaused": True,
    }
    plan = make_plan(route, info, info, "a" * 64, gate, {"blockers": []}, process)
    assert plan["targetPort"] == standby and plan["readyForExistingStrictSwitch"]
    gate["newVideoAdmissionsPaused"] = False
    blocked = make_plan(route, info, info, "a" * 64, gate, {"blockers": []}, process)
    assert not blocked["readyForExistingStrictSwitch"]


@pytest.mark.asyncio
async def test_live_asgi_drain_reports_local_background_jobs(tmp_path):
    database_url = await _create_database(tmp_path / "drain.db")
    settings = _settings(database_url, tmp_path)
    settings.app_port = 8003
    settings.deployment_id = "libtv-classic-lan-0e3d1c8"
    route = tmp_path / "route.json"
    await asyncio.to_thread(route.write_text, '{"version":1,"activeApiPort":8002}')
    settings.classic_api_route_file = route
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/v1/system/classic-api-drain"
            initial = (await client.get(url)).json()
            assert initial["retirementReady"]
            task = asyncio.create_task(asyncio.Event().wait())
            app.state.confirmation_jobs.add(task)
            try:
                blocked = (await client.get(url)).json()
                assert not blocked["retirementReady"]
                assert blocked["workCounts"]["confirmation_jobs"] == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app.state.confirmation_jobs.discard(task)
            assert (await client.get(url)).json()["retirementReady"]
