"""Upload rejection and processing failures on the real migrated upload tables."""

import importlib.util
import io
import json
import logging
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from libtv_agent.api import uploads
from libtv_agent.api.dependencies import get_actor
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.db import Database, get_session
from libtv_agent.domain.models import ApiIdempotencyRecord, Base, ParentTask, StagedUpload, User
from PIL import Image
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

ROOT = Path(__file__).resolve().parents[2]


def image_bytes(fmt="PNG", size=(320, 640)):
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, format=fmt)
    return stream.getvalue()


def install_migrated_upload_tables(connection):
    """Use the installed migration bodies, not model defaults, for these tables."""
    operations = Operations(MigrationContext.configure(connection))
    selected = {"staged_upload", "api_idempotency_record"}
    for table in (StagedUpload.__table__, ApiIdempotencyRecord.__table__):
        table.drop(connection)

    class ScopedOperations:
        def get_bind(self):
            return connection

        def f(self, name):
            return operations.f(name)

        def create_table(self, name, *args, **kwargs):
            if name in selected:
                return operations.create_table(name, *args, **kwargs)

        def create_index(self, name, table, *args, **kwargs):
            if isinstance(table, str) and table in selected:
                operations.create_index(name, table, *args, **kwargs)

        def add_column(self, table, *args, **kwargs):
            if table in selected:
                operations.add_column(table, *args, **kwargs)

        def batch_alter_table(self, table, **kwargs):
            if table in selected:
                return operations.batch_alter_table(table, **kwargs)
            return nullcontext(self)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    for filename in (
        "f59dc2804e6f_phase_one_core_schema.py",
        "2d7f6a9b4c10_fixed_template_workflow.py",
        "b7d9e1f3a502_image_output_asset_provenance.py",
    ):
        spec = importlib.util.spec_from_file_location(
            "upload_schema_" + filename[:12], ROOT / "backend/alembic/versions" / filename
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = ScopedOperations()
        migration.upgrade()
    columns = {
        column["name"]: column for column in inspect(connection).get_columns("staged_upload")
    }
    assert "source_image_output_id" in columns
    assert not columns["object_key"]["nullable"]
    assert columns["object_key"]["default"] is None


@pytest.fixture
async def lab(tmp_path):
    settings = Settings(
        app_env="test", database_url="sqlite+aiosqlite:///:memory:",
        object_store_mode="local", artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner", libtv_adapter_mode="fake", ai_adapter_mode="fake",
        kling_adapter_mode="fake", wavespeed_enabled=False, shared_authentication_enabled=False,
        admin_credentials_file=tmp_path / "credentials.json",
        kling_admin_config_file=tmp_path / "kling.json",
        wavespeed_admin_config_file=tmp_path / "wavespeed.json",
        bach_admin_config_file=tmp_path / "bach.json", _env_file=None,
    )
    app = create_app(settings)
    database = Database(settings.database_url)
    app.state.database = database
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(install_migrated_upload_tables)
    async with database.session_factory() as session:
        actor_id = uuid.uuid4()
        actor = User(id=actor_id, username="upload-test", password_hash="offline-disabled")
        session.add(actor)
        await session.commit()
        app.dependency_overrides[get_actor] = lambda: SimpleNamespace(id=actor_id)

        async def shared_session():
            yield session

        app.dependency_overrides[get_session] = shared_session
        provider_submit = AsyncMock(side_effect=AssertionError("must not submit a provider task"))
        app.state.kling_adapter.submit = provider_submit
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://isolated.test"
        ) as client:
            yield client, app, session
        provider_submit.assert_not_awaited()
        assert await session.scalar(select(func.count()).select_from(ParentTask)) == 0
    await database.dispose()


async def upload(client, data, *, key=None):
    return await client.post(
        "/api/v1/uploads", headers={"Idempotency-Key": key or uuid.uuid4().hex},
        files={"file": ("private-name-token=do-not-log.png", data, "image/png")},
    )


@pytest.mark.parametrize(
    "data,code",
    [
        (b"", "IMAGE_EMPTY"),
        (b"not-an-image", "IMAGE_INVALID"),
        (image_bytes()[:70], "IMAGE_INVALID"),
        (image_bytes("GIF"), "IMAGE_TYPE_UNSUPPORTED"),
        (image_bytes(size=(127, 640)), "IMAGE_DIMENSIONS_INVALID"),
        (image_bytes(size=(8193, 128)), "IMAGE_DIMENSIONS_INVALID"),
    ],
    ids=["empty", "unrecognized", "truncated", "gif", "too-small", "too-large"],
)
async def test_file_rejections_are_specific_and_correlated(lab, data, code, caplog):
    client, app, session = lab
    caplog.set_level(logging.WARNING)
    response = await upload(client, data)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert len(detail["correlationId"]) == 32
    assert not detail["mayHaveBeenCharged"]
    record = next(
        json.loads(item.message.split("upload.failure ", 1)[1])
        for item in caplog.records if item.message.startswith("upload.failure ")
    )
    assert record["correlationId"] == detail["correlationId"]
    assert record["stage"] == "preprocess"
    assert record["sizeBytes"] == len(data)
    assert "private-name" not in caplog.text
    assert "do-not-log" not in caplog.text
    assert await session.scalar(select(1)) == 1
    assert await session.scalar(select(func.count()).select_from(ApiIdempotencyRecord)) == 0
    assert (await upload(client, image_bytes())).status_code == 201


async def test_valid_upload_keeps_receipt_and_size_failures_are_not_422(lab, caplog):
    client, app, session = lab
    data = image_bytes()
    key = uuid.uuid4().hex
    first = await upload(client, data, key=key)
    replay = await upload(client, data, key=key)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert await session.scalar(select(func.count()).select_from(StagedUpload)) == 1
    app.state.settings.max_input_image_bytes = 1024
    failed = await upload(client, b"x" * 1025)
    assert failed.status_code == 413
    assert failed.json()["detail"]["code"] == "INPUT_TOO_LARGE"
    assert "upload.failure" in caplog.text


@pytest.mark.parametrize("mode", ["missing_header", "bad_source", "missing_file"])
async def test_request_validation_is_distinct_and_logs_no_inputs(lab, mode, caplog):
    client, app, session = lab
    caplog.set_level(logging.WARNING)
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    files = {"file": ("test.png", image_bytes(), "image/png")}
    if mode == "missing_header":
        headers = {}
    elif mode == "bad_source":
        headers["X-Source-Image-Output-Id"] = "private-header-value"
    else:
        files = None
    response = await client.post("/api/v1/uploads", headers=headers, files=files)
    detail = response.json()["detail"]
    assert response.status_code == 422
    assert detail["code"] == "REQUEST_VALIDATION_FAILED"
    assert detail["fieldErrors"]
    assert detail["correlationId"] in caplog.text
    assert "request_validation" in caplog.text
    assert "private-header-value" not in caplog.text


@pytest.mark.parametrize("fault", ["value_error", "encoding_io_error"])
async def test_internal_processing_error_does_not_blame_a_valid_image(
    lab, monkeypatch, caplog, fault
):
    client, app, session = lab
    data = image_bytes()
    target = uploads if fault == "value_error" else Image.Image
    attribute = "preprocess_image" if fault == "value_error" else "save"
    original = getattr(target, attribute)
    exception_type = ValueError if fault == "value_error" else OSError

    def fail(*args, **kwargs):
        raise exception_type("secret=private internal details")

    monkeypatch.setattr(target, attribute, fail)
    key = uuid.uuid4().hex
    response = await upload(client, data, key=key)
    detail = response.json()["detail"]
    assert response.status_code == 500
    assert detail["code"] == "UPLOAD_PROCESSING_FAILED"
    assert detail["details"]["exceptionType"] == exception_type.__name__
    assert "尚不能认定图片不合格" in detail["message"]
    assert "private" not in response.text
    assert "private" not in caplog.text
    assert await session.scalar(select(1)) == 1
    monkeypatch.setattr(target, attribute, original)
    assert (await upload(client, data, key=key)).status_code == 201


@pytest.mark.parametrize("error_type", [IntegrityError, OperationalError])
async def test_migrated_upload_record_fault_rolls_back_and_later_upload_continues(
    lab, error_type
):
    client, app, session = lab
    engine = session.get_bind()

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO staged_upload "):
            raise error_type(statement, parameters, RuntimeError("injected upload write fault"))

    event.listen(engine, "before_cursor_execute", fail)
    key = uuid.uuid4().hex
    data = image_bytes()
    try:
        response = await upload(client, data, key=key)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "UPLOAD_PROCESSING_FAILED"
    assert await session.scalar(select(1)) == 1
    assert await session.scalar(select(func.count()).select_from(StagedUpload)) == 0
    assert await session.scalar(select(func.count()).select_from(ApiIdempotencyRecord)) == 0
    assert not list(app.state.settings.artifact_root.glob("**/normalized.jpg"))
    assert (await upload(client, data, key=key)).status_code == 201
