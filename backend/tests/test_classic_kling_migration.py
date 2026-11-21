import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "classic_kling_migrate", ROOT / "scripts/classic_kling_migrate.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def database(path, duplicate=False):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE generation_attempt(id CHAR(32) PRIMARY KEY, variant_id CHAR(32),
                intent_hash TEXT, status TEXT, task_id TEXT, charge_status TEXT);
            CREATE UNIQUE INDEX uq_attempt_active_intent ON generation_attempt(intent_hash)
                WHERE status IN ('LAUNCHING','RUNNING','UNKNOWN_AFTER_SUBMIT','SUCCEEDED');
            INSERT INTO generation_attempt VALUES
                ('a','v','fingerprint','UNKNOWN_AFTER_SUBMIT',NULL,'UNKNOWN');
        """)
        if duplicate:
            connection.execute(
                "INSERT INTO generation_attempt VALUES('b','v','other','RUNNING','cloud','UNKNOWN')"
            )


def test_classic_additive_migration_is_dry_run_backed_up_and_idempotent(tmp_path):
    path = tmp_path / "classic.db"
    database(path)
    before = path.read_bytes()
    assert migration.upgrade_database(path)["status"] == "pending"
    assert path.read_bytes() == before
    result = migration.upgrade_database(path, apply=True)
    assert result["status"] == "upgraded"
    assert Path(result["backup"]).is_file()
    assert migration.upgrade_database(path, apply=True)["status"] == "already_current"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT status,task_id,charge_status FROM generation_attempt"
        ).fetchone() == ("UNKNOWN_AFTER_SUBMIT", None, "UNKNOWN")
        count = connection.execute("SELECT count(*) FROM generation_submission_attempt").fetchone()[
            0
        ]
        assert count == 0
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO generation_attempt(id,variant_id,status) VALUES('b','v','RECONCILING')"
            )


def test_duplicate_legacy_active_variants_fail_closed(tmp_path):
    path = tmp_path / "classic.db"
    database(path, duplicate=True)
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Duplicate active"):
        migration.upgrade_database(path, apply=True)
    assert path.read_bytes() == before
