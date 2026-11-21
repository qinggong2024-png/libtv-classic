"""Download numbering is durable, concurrent and separate from paid generation."""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import test_cutflow_handoff as shared
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.models import ApiIdempotencyRecord, CutflowHandoff, MultiCutflowTask
from libtv_agent.services.cutflow_downloads import download_filename
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

handoff_env = shared.handoff_env
MIGRATION_PATH = Path(__file__).resolve().parents[1] / (
    "alembic/versions/f59dc2804e6f_phase_one_core_schema.py"
)


@pytest.fixture
async def naming_env(handoff_env):
    database, *_ = handoff_env
    spec = importlib.util.spec_from_file_location("naming_table_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def install(connection):
        ApiIdempotencyRecord.__table__.drop(connection)
        operations = Operations(MigrationContext.configure(connection))
        # Execute the table's actual production migration definition. The other
        # tables already exist in the shared, disposable application fixture.
        class TargetInstalled(Exception):
            pass

        class TargetTableOnly:
            def get_bind(self):
                return connection

            def create_table(self, name, *args, **kwargs):
                if name == "api_idempotency_record":
                    operations.create_table(name, *args, **kwargs)
                    raise TargetInstalled

        with patch.object(migration, "op", TargetTableOnly()):
            try:
                migration.upgrade()
            except TargetInstalled:
                pass
        columns = {column["name"]: column for column in inspect(connection).get_columns(
            "api_idempotency_record",
        )}
        assert columns["expires_at"]["default"] is None
        assert columns["response_body_json"]["default"] is None

    async with database.engine.begin() as connection:
        await connection.run_sync(install)
    return handoff_env


def multi(brand="tokyotiger", task_id=None):
    return MultiCutflowTask(
        id=task_id or uuid.uuid4(),
        sources_json=[{"siteProfileId": brand}],
        created_at=datetime(2026, 11, 13, 16, 0, tzinfo=UTC),
        updated_at=datetime(2026, 11, 13, 16, 0, tzinfo=UTC),
    )


def output(identifier="render-1", completed="2026-11-13T16:01:00Z"):
    return {"id": identifier, "created_at": completed}


async def test_names_use_brand_completion_day_and_persist_across_sessions(naming_env):
    database, *_ = naming_env
    row = multi()
    async with database.session_factory() as session:
        assert await download_filename(session, row, output()) == "PMO-TT261114AA01.mp4"
        assert await download_filename(session, row, output("render-2")) == (
            "PMO-TT261114AA02.mp4"
        )
        assert await download_filename(session, multi("pagewings"), output()) == (
            "PMO-PW261114AA01.mp4"
        )
        assert await download_filename(session, row, output(
            "next-day", "2026-11-14T16:00:00Z",
        )) == "PMO-TT261115AA01.mp4"
    async with database.session_factory() as session:
        assert await download_filename(session, multi(task_id=row.id), output()) == (
            "PMO-TT261114AA01.mp4"
        )


async def test_single_and_multi_share_brand_daily_numbering(naming_env):
    database, _, _, ids = naming_env
    single = CutflowHandoff(id=uuid.uuid4(), parent_task_id=ids["task"], manifest_json={})
    async with database.session_factory() as session:
        assert await download_filename(session, single, output()) == "PMO-GC261114AA01.mp4"
        assert await download_filename(session, multi("guidingcross"), output()) == (
            "PMO-GC261114AA02.mp4"
        )


async def test_parallel_downloads_of_same_and_different_outputs_do_not_collide(naming_env):
    database, *_ = naming_env
    row = multi()

    async def name(identifier):
        async with database.session_factory() as session:
            return await download_filename(session, row, output(identifier))

    same = await asyncio.gather(*(name("same") for _ in range(8)))
    assert set(same) == {"PMO-TT261114AA01.mp4"}
    different = await asyncio.gather(*(name(f"other-{index}") for index in range(8)))
    assert set(different) == {f"PMO-TT261114AA{number:02d}.mp4" for number in range(2, 10)}


@pytest.mark.parametrize("failure", [IntegrityError, OperationalError])
async def test_first_write_failure_rolls_back_and_preserves_later_downloads(naming_env, failure):
    database, *_ = naming_env
    engine = database.engine.sync_engine
    failures = []

    def fail_once(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO api_idempotency_record") and not failures:
            failures.append(True)
            raise failure(statement, parameters, RuntimeError("database is locked"))

    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        async with database.session_factory() as session:
            assert await download_filename(session, multi(), output()) == "PMO-TT261114AA01.mp4"
            assert await download_filename(session, multi(), output()) == "PMO-TT261114AA02.mp4"
            assert await session.scalar(select(ApiIdempotencyRecord.response_status)) == 200
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert failures == [True]


async def test_sequence_expands_past_99_and_unknown_brand_keeps_original_name(naming_env):
    database, *_ = naming_env
    async with database.session_factory() as session:
        row = multi()
        assert await download_filename(session, row, output()) == "PMO-TT261114AA01.mp4"
        ledger = await session.scalar(select(ApiIdempotencyRecord))
        ledger.response_body_json = {**ledger.response_body_json, "nextSequence": 100}
        await session.commit()
        assert await download_filename(session, row, output("hundred")) == (
            "PMO-TT261114AA100.mp4"
        )
        assert await download_filename(session, multi("unmapped"), output()) == (
            "edited-render-1.mp4"
        )


async def test_real_download_headers_match_for_single_multi_and_preview(naming_env, tmp_path):
    database, settings, store, ids = naming_env
    media = tmp_path / "edited.mp4"
    media.write_bytes(b"edited-video-fixture")
    key = await store.put_file(media, "cutflow/naming.mp4", "video/mp4")
    single = CutflowHandoff(
        id=uuid.uuid4(), user_id=ids["user"], parent_task_id=ids["task"],
        variant_id=ids["variant"], artifact_id=ids["artifact"], handoff_key="naming",
        manifest_json={"source": {"site_profile_id": "tokyotiger"}},
        outputs_json=[{**output(), "object_key": key}],
    )
    batch = multi()
    batch.user_id = ids["user"]
    batch.idempotency_key = "naming-fixture"
    batch.payload_hash = "a" * 64
    batch.output_json = [{**output(), "object_key": key}]
    single_path = f"/api/v1/cutflow-handoffs/{single.id}/outputs/render-1"
    multi_path = f"/api/v1/cutflow-handoffs/multi/{batch.id}/outputs/render-1"
    async with database.session_factory() as session:
        session.add_all([single, batch])
        await session.commit()
    app = create_app(settings)
    app.state.database = database
    app.state.object_store = store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-User-Id": str(ids["user"])}
        forbidden = await client.get(
            f"{single_path}?download=true", headers={"X-User-Id": str(ids["other"])},
        )
        preview = await client.get(single_path, headers=headers)
        first = await client.get(f"{single_path}?download=true", headers=headers)
        second = await client.get(f"{multi_path}?download=true", headers=headers)
        repeated = await client.get(f"{single_path}?download=true", headers=headers)
    assert forbidden.status_code == 404
    assert "content-disposition" not in preview.headers
    for response, sequence in ((first, 1), (second, 2), (repeated, 1)):
        assert response.status_code == 200
        assert response.content == media.read_bytes()
        assert response.headers["content-disposition"] == (
            f'attachment; filename="PMO-TT261114AA{sequence:02d}.mp4"'
        )
