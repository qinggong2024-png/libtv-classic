import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from libtv_agent.api.image_generations import ImageBatchManifest, _expand_manifest
from libtv_agent.app import create_app
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import ImageGenerationOutput, User
from libtv_agent.services.auth import PasswordService
from libtv_agent.services.image_generations import create_batch_record
from test_image_generation_batches import _app, _manifest


async def test_batch_detail_reads_persisted_state_while_worker_is_busy(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    settings.wavespeed_enabled = False
    headers = {"X-User-Id": settings.dev_user_id}
    async with app.router.lifespan_context(app):
        adapter = app.state.wavespeed_adapter
        adapter.get_prediction = AsyncMock(side_effect=AssertionError("GET must not poll"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/v1/image-generation-batches", headers=headers)
            manifest = _manifest()
            batch = create_batch_record(
                user_id=UUID(settings.dev_user_id),
                idempotency_key="read-only-detail",
                request_hash="hash",
                manifest=manifest,
                item_specs=_expand_manifest(ImageBatchManifest.model_validate(manifest), 2),
            )
            batch.status = "PROCESSING"
            batch.items[0].status = "PROCESSING"
            batch.items[0].provider_task_id = "synthetic-prediction"
            async with app.state.database.session_factory() as session:
                session.add(batch)
                await session.commit()
            url = f"/api/v1/image-generation-batches/{batch.id}"
            async with app.state.image_generation_refresh_lock:
                response = await asyncio.wait_for(client.get(url, headers=headers), timeout=2)
                assert response.status_code == 200
                assert response.json()["status"] == "running"
                assert (await client.get(url)).status_code == 401
                foreign = await client.get(url, headers={"X-User-Id": settings.dev_admin_id})
                assert foreign.status_code == 404
        adapter.get_prediction.assert_not_called()
        assert not adapter.submissions
        assert not adapter.uploads


async def test_history_is_owned_paginated_and_read_only(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    app.state.settings.wavespeed_enabled = False
    headers = {"X-User-Id": settings.dev_user_id}
    other_headers = {"X-User-Id": settings.dev_admin_id}
    async with app.router.lifespan_context(app):
        adapter = app.state.wavespeed_adapter
        adapter.get_prediction = AsyncMock(side_effect=AssertionError("history must not poll"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/v1/image-generation-batches"
            assert (await client.get(url)).status_code == 401
            assert (await client.get(url, headers=headers)).json()["items"] == []
            await client.get(url, headers=other_headers)
            async with app.state.database.session_factory() as session:
                for index, owner in enumerate([settings.dev_user_id] * 3 + [settings.dev_admin_id]):
                    manifest = _manifest(name=f"history-{index}")
                    batch = create_batch_record(
                        user_id=UUID(owner),
                        idempotency_key=f"key-{index}",
                        request_hash="hash",
                        manifest=manifest,
                        item_specs=_expand_manifest(ImageBatchManifest.model_validate(manifest), 2),
                    )
                    batch.created_at = datetime(2026, 10, 15, tzinfo=UTC)
                    batch.id = UUID(int=index + 1)
                    batch.status = "COMPLETED"
                    batch.items[0].status = "COMPLETED"
                    batch.items[0].raw_response_json = {"secret": "private-provider-secret"}
                    batch.items[0].outputs.append(
                        ImageGenerationOutput(
                            order_no=1,
                            remote_url="https://cdn.test/private-signed-url",
                            object_key=f"history/{index}.png",
                        )
                    )
                    session.add(batch)
                await session.commit()
            first = await client.get(url + "?limit=2", headers=headers)
            assert first.status_code == 200, first.text
            body = first.json()
            assert [batch["name"] for batch in body["items"]] == ["history-2", "history-1"]
            assert body["hasMore"] is True
            assert body["nextOffset"] == 2
            assert body["items"][0]["configuration"]["aspectRatio"] == "9:16"
            assert body["items"][0]["items"][0]["outfitName"] == "Look 1"
            assert body["items"][0]["items"][0]["outputs"][0]["url"].startswith(
                "/api/v1/image-generation-outputs/"
            )
            assert "private-" not in first.text
            second = await client.get(url + "?limit=2&offset=2", headers=headers)
            assert [batch["name"] for batch in second.json()["items"]] == ["history-0"]
            assert second.json()["hasMore"] is False
            assert (await client.get(url + "?offset=10", headers=headers)).json()["items"] == []
            other = await client.get(url, headers=other_headers)
            assert [batch["name"] for batch in other.json()["items"]] == ["history-3"]
            for query in ("limit=0", "limit=51", "offset=-1"):
                assert (await client.get(url + "?" + query, headers=headers)).status_code == 422
            # A fresh client has no browser database, but sees the same account's records.
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as fresh:
                assert (await fresh.get(url + "?limit=2", headers=headers)).json() == body
        assert not adapter.uploads
        assert not adapter.submissions
        adapter.get_prediction.assert_not_called()


async def test_separate_browser_sessions_share_only_their_own_history(tmp_path: Path) -> None:
    _, settings = await _app(tmp_path)
    settings = settings.model_copy(
        update={
            "shared_authentication_enabled": True,
            "session_cookie_secure": False,
            "session_secret": "history-test-session-secret-32-bytes",
            "wavespeed_enabled": False,
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        owner_id = UUID(int=101)
        other_id = UUID(int=102)
        async with app.state.database.session_factory() as session:
            for user_id, name in ((owner_id, "owner"), (other_id, "other")):
                session.add(
                    User(
                        id=user_id,
                        username=name,
                        role=UserRole.USER,
                        password_hash=PasswordService().hash("history-test-password"),
                    )
                )
            await session.flush()
            manifest = _manifest()
            batch = create_batch_record(
                user_id=owner_id,
                idempotency_key="session-history",
                request_hash="hash",
                manifest=manifest,
                item_specs=_expand_manifest(ImageBatchManifest.model_validate(manifest), 2),
            )
            batch.status = "FAILED"
            batch.items[0].status = "FAILED"
            session.add(batch)
            await session.commit()
        url = "/api/v1/image-generation-batches"
        for name, expected_count in (("owner", 1), ("owner", 1), ("other", 0)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://history.test"
            ) as browser:
                assert (
                    await browser.get(url, headers={"X-User-Id": str(owner_id)})
                ).status_code == 401
                login = await browser.post(
                    "/api/v1/auth/login",
                    json={
                        "username": name,
                        "password": "history-test-password",
                    },
                )
                assert login.status_code == 200, login.text
                result = await browser.get(url, headers={"X-User-Id": str(owner_id)})
                assert result.status_code == 200
                assert len(result.json()["items"]) == expected_count
        async with app.state.database.session_factory() as session:
            other = await session.get(User, other_id)
            other.page_permissions_json = ["make"]
            await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://history.test"
        ) as browser:
            await browser.post(
                "/api/v1/auth/login",
                json={
                    "username": "other",
                    "password": "history-test-password",
                },
            )
            assert (await browser.get(url)).status_code == 403
