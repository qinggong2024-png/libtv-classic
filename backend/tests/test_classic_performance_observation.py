import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from libtv_agent.services.performance_observation import (
    MAX_KEYS,
    MAX_SAMPLES,
    PerformanceMiddleware,
    PerformanceObservation,
    install_database_observation,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


def test_bounded_samples_and_window():
    now = [0]
    observer = PerformanceObservation(clock=lambda: now[0])
    for index in range(MAX_SAMPLES + 20):
        observer.record("sql", "SELECT", index)
    row = observer.snapshot()["rows"][0]
    assert row["sampleCount"] == MAX_SAMPLES
    assert row["totalSinceStart"] == MAX_SAMPLES + 20
    for index in range(MAX_KEYS + 100):
        observer.record("sql", str(index), 1)
    assert len(observer.rows) <= MAX_KEYS + 8
    now[0] = 901
    assert observer.snapshot()["rows"] == []


@pytest.mark.asyncio
async def test_route_template_only_and_status():
    observer = PerformanceObservation()
    app = FastAPI()
    app.add_middleware(PerformanceMiddleware, observer=observer)

    @app.get("/tasks/{task_id}")
    async def task(task_id: str):
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/tasks/private-id?secret=abc")).status_code == 200
    snapshot = observer.snapshot()
    assert snapshot["rows"][0]["key"] == "GET /tasks/{task_id}"
    assert "private-id" not in str(snapshot)
    assert "secret" not in str(snapshot)


def test_real_sqlite_writer_and_lock_are_observed(tmp_path):
    path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": .01})
    observer = PerformanceObservation()
    install_database_observation(engine, observer)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE example (id INTEGER, private TEXT)"))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO example VALUES (1, 'private-payload')"))
        active = observer.snapshot()["activeTransactions"]
        assert active[0]["writePhaseMs"] is not None
    lock = sqlite3.connect(path)
    try:
        lock.execute("BEGIN IMMEDIATE")
        with engine.connect() as conn, pytest.raises(OperationalError):
            conn.execute(text("INSERT INTO example VALUES (2, 'secret-payload')"))
    finally:
        lock.close()
        engine.dispose()
    snapshot = observer.snapshot()
    assert snapshot["activeTransactions"] == []
    assert sum(row["locksSinceStart"] for row in snapshot["rows"]) == 1
    assert any(row["kind"] == "write_phase" for row in snapshot["rows"])
    assert "private-payload" not in str(snapshot)
    assert "secret-payload" not in str(snapshot)


def test_route_template_not_raw_path_on_unknown_route():
    from libtv_agent.services.performance_observation import route_key

    assert route_key({"method": "GET", "path": "/secret"}) == "GET unmatched"
    assert route_key({"method": "GET", "route": SimpleNamespace(path="/x/{id}")}) == "GET /x/{id}"


@pytest.mark.asyncio
async def test_download_metadata_connection_is_observed_and_bounded(tmp_path, monkeypatch):
    from libtv_agent import db

    observer = PerformanceObservation()
    monkeypatch.setattr(
        db, "install_database_observation",
        lambda engine: install_database_observation(engine, observer),
    )
    path = tmp_path / "metadata.db"
    database = db.Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    try:
        assert database.download_metadata_engine is not database.engine
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE example (id INTEGER)"))
        lock = sqlite3.connect(path)
        try:
            lock.execute("BEGIN IMMEDIATE")
            async with database.download_metadata_session_factory() as session:
                with pytest.raises(OperationalError):
                    await session.execute(text("INSERT INTO example VALUES (1)"))
                await session.rollback()
        finally:
            lock.close()
        rows = observer.snapshot()["rows"]
        assert sum(row["locksSinceStart"] for row in rows) == 1
        assert any(row["kind"] == "sql" and row["key"].endswith("INSERT") for row in rows)
        assert observer.snapshot()["activeTransactions"] == []
    finally:
        await database.dispose()
