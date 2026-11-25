"""Fault injection for the 2026-11-24 Classic outage; no live DB or provider."""

import sqlite3
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.kling_official_api import KlingApiError, KlingOfficialApiAdapter
from libtv_agent.adapters.kling_submission_receipts import KlingSubmissionReceipt
from libtv_agent.adapters.video_provider import ProviderSubmissionResult, VideoGenerationRequest
from libtv_agent.api.health import router as health_router
from libtv_agent.app import create_app
from libtv_agent.db import Database
from libtv_agent.domain.models import CutflowHandoff
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_cutflow_downloads import handoff_env as handoff_env
from test_cutflow_downloads import multi, output
from test_cutflow_downloads import naming_env as naming_env
from test_kling_api_adapter import _settings


async def test_api_receipt_persists_before_return_and_prevents_second_paid_call(tmp_path):
    settings = _settings(tmp_path)
    adapter = KlingOfficialApiAdapter(settings)
    account = await adapter.submission_account_id()
    request = VideoGenerationRequest(
        "private prompt", None, external_task_id="exact-input",
        submission_operation_key="attempt:generation", submission_account_id=account,
        submission_fingerprint="exact-input",
    )
    receipt = KlingSubmissionReceipt(
        settings.runner_root, "attempt:generation", adapter.get_provider_name(),
        account, "exact-input",
    )

    async def accepted(_request):
        assert receipt.load()["status"] == "STARTED"
        return ProviderSubmissionResult("cloud-id", "submitted", 8, "kling-3.0-omni", {})

    adapter._submit = AsyncMock(side_effect=accepted)
    result = await adapter.submit(request)
    assert result.provider_task_id == receipt.load()["providerTaskId"] == "cloud-id"
    assert receipt.load()["status"] == "ACCEPTED"
    assert "private prompt" not in receipt.path.read_text()
    with pytest.raises(KlingApiError) as error:
        await adapter.submit(request)
    assert error.value.provider_task_id == "cloud-id"
    assert error.value.uncertain
    adapter._submit.assert_awaited_once()


async def test_api_receipt_disk_failure_blocks_paid_call(tmp_path, monkeypatch):
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    account = await adapter.submission_account_id()
    request = VideoGenerationRequest(
        "private prompt", None, external_task_id="input",
        submission_operation_key="attempt:generation", submission_account_id=account,
        submission_fingerprint="input",
    )
    adapter._submit = AsyncMock()

    def unavailable(_self):
        raise OSError("synthetic disk unavailable")

    monkeypatch.setattr(KlingSubmissionReceipt, "begin", unavailable)
    with pytest.raises(KlingApiError) as error:
        await adapter.submit(request)
    assert not error.value.submitted
    adapter._submit.assert_not_awaited()


async def test_sqlite_write_health_distinguishes_readable_from_writable(tmp_path):
    path = tmp_path / "scratch.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE test (id INTEGER)")
    database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    app = FastAPI()
    app.state.database = database
    app.include_router(health_router)
    lock = sqlite3.connect(path)
    try:
        assert await database.sqlite_write_ready() is True
        lock.execute("BEGIN IMMEDIATE")
        assert lock.execute("SELECT COUNT(*) FROM test").fetchone() == (0,)
        assert await database.sqlite_write_ready() is False
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/health/ready")
            assert result.status_code == 503
            assert result.json()["checks"]["sqliteWrite"] == "unavailable"
        lock.rollback()
        assert await database.sqlite_write_ready() is True
    finally:
        lock.close()
        await database.dispose()


@pytest.mark.parametrize('real_lock', [False, True])
async def test_existing_files_download_even_when_naming_and_audit_writes_fail(
    naming_env, tmp_path, real_lock,
):
    database, settings, store, ids = naming_env
    media = tmp_path / "edited.mp4"
    media.write_bytes(b"existing-edited-video")
    key = await store.put_file(media, "cutflow/fault.mp4", "video/mp4")
    single = CutflowHandoff(
        id=uuid.uuid4(), user_id=ids["user"], parent_task_id=ids["task"],
        variant_id=ids["variant"], artifact_id=ids["artifact"], handoff_key="fault-fixture",
        manifest_json={"source": {"site_profile_id": "tokyotiger"}},
        outputs_json=[{**output(), "object_key": key}],
    )
    batch = multi()
    batch.user_id = ids["user"]
    batch.idempotency_key = "fault-fixture"
    batch.payload_hash = "b" * 64
    batch.output_json = [{**output(), "object_key": key}]
    paths = [
        f"/api/v1/cutflow-handoffs/{single.id}/outputs/render-1",
        f"/api/v1/cutflow-handoffs/multi/{batch.id}/outputs/render-1",
    ]
    async with database.session_factory() as session:
        session.add_all([single, batch])
        await session.commit()

    def locked(connection, cursor, statement, parameters, context, many):
        if statement.startswith(("INSERT INTO api_idempotency_record", "INSERT INTO audit_event")):
            raise OperationalError(statement, parameters, RuntimeError("database is locked"))

    engine = database.download_metadata_engine.sync_engine
    app = create_app(settings)
    app.state.database = database
    app.state.object_store = store
    lock = None
    if real_lock:
        lock = sqlite3.connect(database.engine.url.database, timeout=1)
        lock.execute("BEGIN IMMEDIATE")
    else:
        event.listen(engine, "before_cursor_execute", locked)
    started = time.monotonic()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = {"X-User-Id": str(ids["user"])}
            other = {"X-User-Id": str(ids["other"])}
            for path in paths:
                denied = await client.get(path + "?download=true", headers=other)
                assert denied.status_code == 404
                result = await client.get(path + "?download=true", headers=owner)
                assert result.status_code == 200
                assert result.content == media.read_bytes()
                assert 'filename="edited-' in result.headers["content-disposition"]
            original = await client.get(
                f"/api/v1/downloads/{ids['artifact']}?download=true", headers=owner,
            )
            assert original.status_code == 200
            assert original.content == b"generated-video"
        if real_lock:
            assert time.monotonic() - started < 4
    finally:
        if lock is not None:
            lock.rollback()
            lock.close()
        else:
            event.remove(engine, "before_cursor_execute", locked)
