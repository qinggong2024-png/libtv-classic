import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.kling_official_api import ApiHttpResponse, KlingOfficialApiAdapter
from libtv_agent.adapters.kling_provider import create_kling_adapter
from libtv_agent.adapters.libtv import create_libtv_adapter
from libtv_agent.adapters.object_store import create_object_store
from libtv_agent.adapters.video_provider import ProviderAvailability
from libtv_agent.adapters.wavespeed import WaveSpeedHttpResponse
from libtv_agent.adapters.wavespeed_video import WaveSpeedVideoAdapter
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import AuditEvent, Base, User
from libtv_agent.runner.supervisor import RunnerSupervisor
from libtv_agent.services.kling_config import load_kling_admin_config
from libtv_agent.services.wavespeed_config import load_wavespeed_admin_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OPERATOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000088")
API_KEY = "kling-test-secret-key-never-send"


@pytest.mark.asyncio
async def test_cli_route_can_be_enabled_without_an_api_key(tmp_path: Path) -> None:
    database_url = await initialized_database(tmp_path, "kling-cli-route.db")
    settings = kling_settings(database_url, tmp_path)
    app = create_app(settings)
    headers = {"X-User-Id": str(ADMIN_ID)}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            switched = await client.patch(
                "/api/v1/admin/kling/config",
                headers=headers,
                json={"backend": "cli", "enabled": True},
            )
            assert switched.status_code == 200, switched.text
            assert switched.json()["backend"] == "cli"
            assert switched.json()["provider"] == "kling_official_cli"
            assert switched.json()["enabled"] is True
            assert switched.json()["apiKeyConfigured"] is False
            assert settings.kling_provider_submission_enabled("kling_official_cli")
    reloaded = kling_settings(database_url, tmp_path)
    assert load_kling_admin_config(reloaded)
    assert reloaded.kling_provider_backend == "cli"
    assert reloaded.kling_cli_enabled


def kling_settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=database_url,
        object_store_mode="local",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        admin_credentials_file=tmp_path / "runtime" / "admin-credentials.json",
        kling_admin_config_file=tmp_path / "runtime" / "kling-config.json",
        wavespeed_admin_config_file=tmp_path / "runtime" / "wavespeed-config.json",
        kling_provider_backend="api",
        kling_cli_enabled=False,
        kling_api_enabled=False,
        kling_api_key=None,
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_admin_can_switch_new_video_work_to_wavespeed(tmp_path: Path) -> None:
    database_url = await initialized_database(tmp_path, "wavespeed-video-switch.db")
    settings = kling_settings(database_url, tmp_path)
    app = create_app(settings)
    headers = {"X-User-Id": str(ADMIN_ID)}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            wavespeed = await client.patch(
                "/api/v1/admin/wavespeed/config",
                headers=headers,
                json={
                    "apiKey": "wavespeed-video-test-key",
                    "enabled": True,
                    "videoModel": "kwaivgi/kling-video-o3-std/reference-to-video",
                    "videoDuration": 8,
                    "videoAspectRatio": "16:9",
                    "videoSound": True,
                },
            )
            assert wavespeed.status_code == 200, wavespeed.text

            async def verify_http(
                method: str,
                url: str,
                request_headers: dict[str, str],
                body: bytes | None,
                request_timeout: float,
            ) -> WaveSpeedHttpResponse:
                del method, url, request_headers, body, request_timeout
                return WaveSpeedHttpResponse(404, {}, b'{"message":"not found"}')

            app.state.wavespeed_adapter._http_request = verify_http
            verified = await client.post("/api/v1/admin/wavespeed/verify", headers=headers)
            assert verified.status_code == 200, verified.text
            assert verified.json()["available"] is True

            switched = await client.patch(
                "/api/v1/admin/kling/config",
                headers=headers,
                json={"backend": "wavespeed"},
            )
            assert switched.status_code == 200, switched.text
            assert switched.json()["backend"] == "wavespeed"
            assert switched.json()["provider"] == "wavespeed_video"
            assert isinstance(app.state.kling_adapter, WaveSpeedVideoAdapter)
            app.state.kling_adapter._availability = ProviderAvailability(
                status="WAVESPEED_CONFIGURED_UNVERIFIED",
                available=False,
                authenticated=False,
                reason="stale wrapper state",
            )

            system = await client.get("/api/v1/system/status", headers=headers)
            assert system.status_code == 200
            assert system.json()["kling"]["backend"] == "wavespeed"
            assert system.json()["kling"]["provider"] == "wavespeed_video"
            assert system.json()["videoGeneration"] == system.json()["kling"]
            assert system.json()["videoGeneration"]["available"] is True
            assert system.json()["videoGeneration"]["authenticated"] is True
            assert system.json()["videoGeneration"]["model"] == (
                "kwaivgi/kling-video-o3-std/reference-to-video"
            )
            assert system.json()["videoGeneration"]["imageInputLimit"] == 7

    reloaded = kling_settings(database_url, tmp_path)
    assert load_kling_admin_config(reloaded) is True
    assert load_wavespeed_admin_config(reloaded) is True
    assert reloaded.kling_provider_backend == "wavespeed"
    assert reloaded.wavespeed_video_model == ("kwaivgi/kling-video-o3-std/reference-to-video")
    assert isinstance(create_kling_adapter(reloaded), WaveSpeedVideoAdapter)


@pytest.mark.asyncio
async def test_kling_billing_read_only_admin_route_denies_regular_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = await initialized_database(tmp_path, "kling-billing.db")
    settings = kling_settings(database_url, tmp_path)
    app = create_app(settings)
    calls: list[int] = []

    async def fake_report(session, *, limit: int):
        del session
        calls.append(limit)
        return {"source": "stored_kling_api_task_receipts", "items": [],
                "totals": [], "receiptCount": 0}

    monkeypatch.setattr("libtv_agent.api.admin.kling_billing_report", fake_report)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unauth = await client.get("/api/v1/admin/kling/billing")
            assert unauth.status_code == 401
            forbidden = await client.get(
                "/api/v1/admin/kling/billing", headers={"X-User-Id": str(USER_ID)},
            )
            assert forbidden.status_code == 403
            valid = await client.get(
                "/api/v1/admin/kling/billing?limit=17",
                headers={"X-User-Id": str(ADMIN_ID)},
            )
            assert valid.status_code == 200
            assert valid.json()["source"] == "stored_kling_api_task_receipts"
            assert calls == [17]
            invalid = await client.get(
                "/api/v1/admin/kling/billing?limit=501",
                headers={"X-User-Id": str(ADMIN_ID)},
            )
            assert invalid.status_code == 422
            assert calls == [17]


async def initialized_database(tmp_path: Path, name: str) -> str:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["wavespeed", "bach"])
@pytest.mark.parametrize(
    ("enabled", "has_key", "http_status", "expected_status"),
    [
        (True, True, 200, "KLING_API_READY"),
        (False, True, 200, "KLING_API_DISABLED"),
        (True, False, 200, "KLING_API_KEY_MISSING"),
        (True, True, 401, "KLING_API_AUTHENTICATION_FAILED"),
    ],
)
async def test_official_kling_status_and_refresh_are_independent_of_video_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    enabled: bool,
    has_key: bool,
    http_status: int,
    expected_status: str,
) -> None:
    database_url = await initialized_database(tmp_path, "official-status.db")
    settings = kling_settings(database_url, tmp_path)
    settings.kling_provider_backend = backend
    settings.bach_enabled = False
    settings.wavespeed_enabled = False
    app = create_app(settings)
    calls: list[tuple[str, str, bytes | None]] = []

    async def official_http(method, url, headers, body, request_timeout):
        del request_timeout
        assert headers["Authorization"] == f"Bearer {API_KEY}"
        calls.append((method, url, body))
        return ApiHttpResponse(
            http_status,
            {},
            json.dumps({"code": 0, "message": "test", "data": []}).encode(),
        )

    monkeypatch.setattr(
        KlingOfficialApiAdapter,
        "_default_http_request",
        staticmethod(official_http),
    )
    async with app.router.lifespan_context(app):
        settings.kling_api_enabled = enabled
        settings.kling_api_key = API_KEY if has_key else None
        active_adapter = app.state.kling_adapter
        active_check = AsyncMock(side_effect=AssertionError("checked the selected video route"))
        active_refresh = AsyncMock(side_effect=AssertionError("refreshed the selected video route"))
        monkeypatch.setattr(active_adapter, "check_availability", active_check)
        monkeypatch.setattr(active_adapter, "refresh_capabilities", active_refresh)
        headers = {"X-User-Id": str(ADMIN_ID)}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path, method in [("status", "GET"), ("capabilities/refresh", "POST")]:
                response = await client.request(
                    method,
                    f"/api/v1/admin/kling/{path}",
                    headers=headers,
                )
                ready = expected_status == "KLING_API_READY"
                if method == "POST" and not ready:
                    assert response.status_code == 409
                    assert response.json()["detail"]["code"] == expected_status
                else:
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["backend"] == "api"
                    assert result["provider"] == "kling_official_api"
                    assert result["enabled"] is enabled
                    assert result["status"] == expected_status
                    assert result["available"] is ready
                    assert result["authenticated"] is ready
                    assert result["verificationMode"] == "official_read_only_task_query"
                    assert result["verificationMayGenerateOrCharge"] is False
                    assert "wavespeed" not in (result["cliVersion"] or "")
                assert API_KEY not in response.text
                assert app.state.kling_adapter is active_adapter
                assert settings.kling_provider_backend == backend
            active_check.assert_not_awaited()
            active_refresh.assert_not_awaited()
            assert len(calls) == (2 if enabled and has_key else 0)
            assert all(method == "GET" and body is None for method, _, body in calls)
            assert all(url.startswith("https://api-beijing.klingai.com/") for _, url, _ in calls)
            # The system status must still describe the selected video route.
            system = await client.get("/api/v1/system/status", headers=headers)
            assert system.json()["videoGeneration"]["backend"] == backend
            if expected_status == "KLING_API_READY":
                async with app.state.database.session_factory() as session:
                    audit = await session.scalar(
                        select(AuditEvent).where(
                            AuditEvent.action == "KLING_CAPABILITIES_REFRESHED"
                        )
                    )
                    assert audit.entity_id == "kling_official_api"


@pytest.mark.asyncio
async def test_admin_can_update_reload_switch_and_clear_kling_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    database_url = await initialized_database(tmp_path, "kling-config.db")
    settings = kling_settings(database_url, tmp_path)
    config_file = settings.kling_admin_config_file
    app = create_app(settings)
    headers = {"X-User-Id": str(ADMIN_ID)}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            initial = await client.get("/api/v1/admin/kling/config", headers=headers)
            assert initial.status_code == 200
            assert initial.json() == {
                "backend": "api",
                "provider": "kling_official_api",
                "enabled": False,
                "apiKeyConfigured": False,
                "baseUrl": "https://api-beijing.klingai.com",
                "callbackUrl": None,
                "model": "kling-4.0",
                "duration": 10,
                "aspectRatio": "9:16",
                "resolution": "1080p",
                "audio": "off",
                "multiShot": True,
                "maxSubmitConcurrency": 1,
                "maxPollConcurrency": 4,
                "updatedAt": None,
            }
            assert "apiKey" not in initial.json()

            updated = await client.patch(
                "/api/v1/admin/kling/config",
                headers=headers,
                json={
                    "backend": "api",
                    "apiKey": API_KEY,
                    "baseUrl": "https://api-singapore.klingai.com",
                    "callbackUrl": "https://example.test/api/v1/kling/callback",
                    "model": "kling-4.0",
                    "duration": 15,
                    "aspectRatio": "16:9",
                    "resolution": "1080p",
                    "audio": "native",
                    "multiShot": False,
                    "maxSubmitConcurrency": 3,
                    "maxPollConcurrency": 12,
                    "enabled": True,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["backend"] == "api"
            assert updated.json()["provider"] == "kling_official_api"
            assert updated.json()["enabled"] is True
            assert updated.json()["apiKeyConfigured"] is True
            assert updated.json()["baseUrl"] == "https://api-singapore.klingai.com"
            assert updated.json()["callbackUrl"] == "https://example.test/api/v1/kling/callback"
            assert updated.json()["model"] == "kling-4.0"
            assert updated.json()["duration"] == 15
            assert updated.json()["aspectRatio"] == "16:9"
            assert updated.json()["resolution"] == "1080p"
            assert updated.json()["audio"] == "native"
            assert updated.json()["multiShot"] is False
            assert updated.json()["maxSubmitConcurrency"] == 3
            assert updated.json()["maxPollConcurrency"] == 12
            assert updated.json()["updatedAt"] is not None
            assert API_KEY not in updated.text
            assert "apiKey" not in updated.json()

            assert settings.kling_provider_backend == "api"
            assert settings.kling_api_enabled is True
            assert settings.kling_api_key == API_KEY
            assert settings.kling_api_base_url == "https://api-singapore.klingai.com"
            assert settings.kling_api_callback_url == "https://example.test/api/v1/kling/callback"
            assert settings.kling_api_duration == 15
            assert settings.kling_api_aspect_ratio == "16:9"
            assert settings.kling_api_resolution == "1080p"
            assert settings.kling_api_audio == "native"
            assert settings.kling_api_multi_shot is False
            assert settings.kling_api_max_submit_concurrency == 3
            assert settings.kling_api_max_poll_concurrency == 12
            assert isinstance(app.state.kling_adapter, KlingOfficialApiAdapter)

            verification_calls: list[tuple[str, bytes | None]] = []

            async def verify_without_generation(
                method: str,
                url: str,
                request_headers: dict[str, str],
                body: bytes | None,
                request_timeout: float,
            ) -> ApiHttpResponse:
                del url, request_headers, request_timeout
                verification_calls.append((method, body))
                return ApiHttpResponse(
                    200,
                    {},
                    json.dumps({"code": 0, "message": "ok", "data": []}).encode(),
                )

            app.state.kling_adapter._http_request = verify_without_generation
            status = await client.get("/api/v1/admin/kling/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["backend"] == "api"
            assert status.json()["provider"] == "kling_official_api"
            assert status.json()["status"] == "KLING_API_READY"
            assert status.json()["available"] is True
            assert status.json()["authenticated"] is True
            assert status.json()["verificationMode"] == "official_read_only_task_query"
            assert status.json()["verificationMayGenerateOrCharge"] is False
            assert verification_calls == [("GET", None)]
            assert API_KEY not in status.text

            async with app.state.database.session_factory() as session:
                audit = await session.scalar(
                    select(AuditEvent).where(AuditEvent.action == "KLING_CONFIG_UPDATED")
                )
                assert audit is not None
                audit_payload = {
                    "action": audit.action,
                    "entityType": audit.entity_type,
                    "entityId": audit.entity_id,
                    "fromState": audit.from_state,
                    "toState": audit.to_state,
                    "reason": audit.reason,
                    "evidence": audit.evidence_refs_json,
                }
                assert API_KEY not in json.dumps(audit_payload, ensure_ascii=False)

            assert config_file.is_file()
            assert API_KEY not in config_file.read_text(encoding="utf-8")

            # The API and the independent Runner process must be able to hydrate the
            # same persisted configuration before constructing their adapter.
            reloaded = kling_settings(database_url, tmp_path)
            assert load_kling_admin_config(reloaded) is True
            assert reloaded.kling_provider_backend == "api"
            assert reloaded.kling_api_enabled is True
            assert reloaded.kling_api_key == API_KEY
            assert reloaded.kling_api_base_url == "https://api-singapore.klingai.com"
            assert reloaded.kling_api_callback_url == "https://example.test/api/v1/kling/callback"
            assert reloaded.kling_api_model == "kling-4.0"
            assert reloaded.kling_api_duration == 15
            assert reloaded.kling_api_aspect_ratio == "16:9"
            assert reloaded.kling_api_resolution == "1080p"
            assert reloaded.kling_api_audio == "native"
            assert reloaded.kling_api_multi_shot is False
            assert reloaded.kling_api_max_submit_concurrency == 3
            assert reloaded.kling_api_max_poll_concurrency == 12
            assert reloaded.kling_cli_default_model == "kling-video-v3_0_omni"
            assert reloaded.kling_cli_default_duration == 10
            assert reloaded.kling_cli_default_aspect_ratio == "9:16"
            assert reloaded.kling_cli_max_submit_concurrency == 1
            assert reloaded.kling_cli_max_query_concurrency == 4
            runner_adapter = create_kling_adapter(reloaded)
            assert isinstance(runner_adapter, KlingOfficialApiAdapter)
            assert (
                runner_adapter.availability_snapshot().status == "KLING_API_CONFIGURED_UNVERIFIED"
            )
            runner_supervisor = RunnerSupervisor(
                create_libtv_adapter(reloaded),
                create_object_store(reloaded),
                reloaded,
                kling_adapter=runner_adapter,
            )

            cleared = await client.patch(
                "/api/v1/admin/kling/config",
                headers=headers,
                json={
                    "enabled": False,
                    "clearApiKey": True,
                    "clearCallbackUrl": True,
                },
            )
            assert cleared.status_code == 200
            assert cleared.json()["backend"] == "api"
            assert cleared.json()["provider"] == "kling_official_api"
            assert cleared.json()["enabled"] is False
            assert cleared.json()["apiKeyConfigured"] is False
            assert settings.kling_api_key is None
            assert settings.kling_api_callback_url is None
            assert isinstance(app.state.kling_adapter, KlingOfficialApiAdapter)
            assert API_KEY not in config_file.read_text(encoding="utf-8")

            # Model the Runner's signature-change branch: reload the shared file,
            # rebuild the adapter, and replace the supervisor's active provider.
            assert load_kling_admin_config(reloaded) is True
            assert reloaded.kling_provider_backend == "api"
            assert reloaded.kling_api_key is None
            replacement = create_kling_adapter(reloaded)
            runner_supervisor.replace_kling_adapter(replacement)
            assert isinstance(runner_supervisor.kling_adapter, KlingOfficialApiAdapter)


@pytest.mark.asyncio
async def test_only_admin_can_modify_kling_config(tmp_path: Path) -> None:
    database_url = await initialized_database(tmp_path, "kling-config-permissions.db")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            User.__table__.insert().values(
                id=OPERATOR_ID,
                username="kling-operator",
                password_hash="disabled",
                role=UserRole.OPERATOR.value,
                is_active=True,
            )
        )
    await engine.dispose()
    settings = kling_settings(database_url, tmp_path)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for actor_id in (USER_ID, OPERATOR_ID):
                actor_headers = {"X-User-Id": str(actor_id)}
                for path, method in [("status", "GET"), ("capabilities/refresh", "POST")]:
                    forbidden_status = await client.request(
                        method,
                        f"/api/v1/admin/kling/{path}",
                        headers=actor_headers,
                    )
                    assert forbidden_status.status_code == 403
                forbidden = await client.patch(
                    "/api/v1/admin/kling/config",
                    headers=actor_headers,
                    json={"backend": "api", "enabled": True, "apiKey": API_KEY},
                )
                assert forbidden.status_code == 403

            current = await client.get(
                "/api/v1/admin/kling/config",
                headers={"X-User-Id": str(ADMIN_ID)},
            )
            assert current.status_code == 200
            assert current.json()["backend"] == "api"
            assert current.json()["enabled"] is False
            assert current.json()["apiKeyConfigured"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"backend": "browser"},
        {"model": "kling-unsupported"},
        {"duration": 2},
        {"duration": 31},
        {"aspectRatio": "4:3"},
        {"resolution": "8k"},
        {"audio": "forced"},
        {"maxSubmitConcurrency": 0},
        {"maxSubmitConcurrency": 21},
        {"maxPollConcurrency": 0},
        {"maxPollConcurrency": 51},
        {"apiKey": "short"},
        {"apiKey": API_KEY, "clearApiKey": True},
        {"baseUrl": "https://api.example.test"},
        {"callbackUrl": "http://example.test/kling/callback"},
        {
            "callbackUrl": "https://example.test/kling/callback",
            "clearCallbackUrl": True,
        },
        {"backend": "api", "enabled": True},
    ],
)
async def test_invalid_kling_config_is_rejected_without_mutation(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    database_url = await initialized_database(tmp_path, "invalid-kling-config.db")
    settings = kling_settings(database_url, tmp_path)
    app = create_app(settings)
    headers = {"X-User-Id": str(ADMIN_ID)}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            rejected = await client.patch(
                "/api/v1/admin/kling/config",
                headers=headers,
                json=payload,
            )
            assert rejected.status_code == 422
            current = await client.get("/api/v1/admin/kling/config", headers=headers)
            assert current.status_code == 200
            assert current.json()["backend"] == "api"
            assert current.json()["enabled"] is False
            assert current.json()["apiKeyConfigured"] is False
            assert isinstance(app.state.kling_adapter, KlingOfficialApiAdapter)
