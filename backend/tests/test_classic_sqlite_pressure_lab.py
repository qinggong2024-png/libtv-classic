"""P0-2 isolated DELETE/WAL concurrency and paid-intent uniqueness tests."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

import pytest
from libtv_agent.db import Database

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "scripts" / "classic_sqlite_pressure_lab.py"
SPEC = importlib.util.spec_from_file_location("classic_sqlite_pressure_lab", LAB)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rehearsal_20_synthetic_cloud_jobs_does_not_duplicate_intents():
    result = MODULE.run_rehearsal(cloud_tasks=20, repeats=2)
    assert len(result) == 4
    for run in result:
        assert run["passed"], run
        assert run["events"] == 80
        assert run["simulated_unique_paid_intents"] == 20
        assert run["simulated_cut_receipts"] == 20
        assert not run["worker_errors"]
        assert run["integrity"] == "ok"


def test_wal_does_not_eliminate_two_writer_contention():
    with tempfile.TemporaryDirectory(prefix="allwe-writer-race-test-") as directory:
        path = Path(directory) / "writer-race.db"
        connection = sqlite3.connect(path)
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value INTEGER)")
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO test VALUES (1, 1)")
        competing = sqlite3.connect(path, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.execute("BEGIN IMMEDIATE")
        finally:
            competing.close()
        connection.commit()
        connection.close()
        recovered = sqlite3.connect(path)
        try:
            recovered.execute("INSERT INTO test VALUES (2,2)")
            recovered.commit()
            assert recovered.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 2
        finally:
            recovered.close()


@pytest.mark.asyncio
async def test_production_database_defaults_do_not_switch_to_wal(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'untouched.db').as_posix()}")
    try:
        async with db.engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE local_lab_sentinel (id INTEGER PRIMARY KEY)"
            )
        async with db.engine.connect() as connection:
            mode = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
        assert mode.lower() in ("delete", "memory")
    finally:
        await db.dispose()
