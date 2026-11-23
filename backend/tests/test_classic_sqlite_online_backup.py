"""Live-SQLite-compatible backup rehearsal with only synthetic records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from classic_sqlite_online_backup import make_online_backup  # noqa: E402


def make_source(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE parent_task (id TEXT PRIMARY KEY, phase TEXT)")
        conn.execute("CREATE TABLE generation_attempt (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE generation_command (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE multi_cutflow_task (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO parent_task VALUES ('synthetic-only', 'DONE')")
        conn.execute("INSERT INTO generation_attempt VALUES ('a', 'SUCCEEDED')")
        conn.execute("INSERT INTO generation_command VALUES ('c', 'SUCCEEDED')")
        conn.execute("INSERT INTO multi_cutflow_task VALUES ('cut', 'DONE')")
        conn.commit()


def test_live_consistent_backup_is_verified_and_source_is_unmodified(tmp_path):
    source = tmp_path / "classic.db"
    make_source(source)
    protected = tmp_path / "private-runtime"
    protected.mkdir()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = make_online_backup(source, protected)
    destination = Path(result["backupDirectory"])
    assert result["integrity"] == "ok"
    assert result["restoredCloneReadable"]
    assert result["counts"]["parent_task"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert result["sha256"] == hashlib.sha256((destination / "classic.db").read_bytes()).hexdigest()
    assert json.loads((destination / "manifest.json").read_text())["sha256"] == result["sha256"]
    with sqlite3.connect(destination / "classic.db") as restored:
        assert restored.execute("SELECT phase FROM parent_task").fetchone()[0] == "DONE"


def test_backup_fails_closed_with_missing_task_tables(tmp_path):
    source = tmp_path / "classic.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE not_classic (x INTEGER)")
    protected = tmp_path / "private-runtime"
    protected.mkdir()
    with pytest.raises(RuntimeError, match="missing Classic task"):
        make_online_backup(source, protected)
    assert list(protected.iterdir()) == []





def test_classic_production_layout_rejects_unprotected_backup_destination(tmp_path):
    prod = tmp_path / "classic-lan"
    prod.mkdir()
    source = prod / "classic.db"
    make_source(source)
    unprotected = tmp_path / "public"
    unprotected.mkdir()
    with pytest.raises(ValueError, match="private runtime"):
        make_online_backup(source, unprotected)

def test_rejects_backup_overlapping_live_db_directory(tmp_path):
    source = tmp_path / "classic.db"
    make_source(source)
    with pytest.raises(ValueError, match="separate protected directory"):
        make_online_backup(source, tmp_path)
