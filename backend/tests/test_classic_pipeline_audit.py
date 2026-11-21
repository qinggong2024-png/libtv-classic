"""Regression tests for the classic read-only throughput audit."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/classic_pipeline_audit.py"
SPEC = importlib.util.spec_from_file_location("classic_pipeline_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def stamp(delta: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta)).replace(
        tzinfo=None
    ).isoformat(sep=" ")


def test_summarize_timing_and_clock() -> None:
    assert module.summarize([])["count"] == 0
    assert module.summarize([2, 8])["p50_seconds"] == 5
    assert module.summarize([2, 8])["p90_seconds"] == 8
    assert module.elapsed(stamp(-30), stamp(-10)) == pytest.approx(20, abs=0.01)
    assert module.elapsed(None, stamp(-10)) is None


def test_readonly_snapshot_does_not_update_database(tmp_path: Path) -> None:
    path = tmp_path / "audit-fixture.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE parent_task (
            id TEXT, created_at TEXT, analysis_started_at TEXT,
            analysis_heartbeat_at TEXT, analysis_stage TEXT,
            source_upload_duration_seconds REAL, workflow_kind TEXT,
            phase TEXT, phase_reason_code TEXT
        );
        CREATE TABLE generation_variant (id TEXT, parent_task_id TEXT);
        CREATE TABLE generation_attempt (
            id TEXT, variant_id TEXT, provider TEXT, task_id TEXT,
            status TEXT, created_at TEXT, submitted_at TEXT, completed_at TEXT
        );
        CREATE TABLE generation_command (
            id TEXT, attempt_id TEXT, status TEXT
        );
        CREATE TABLE generation_submission_attempt (finished_at TEXT);
        CREATE TABLE download_artifact (
            attempt_id TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE multi_cutflow_task (
            status TEXT, created_at TEXT, error_code TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO parent_task VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "task1", stamp(-600), stamp(-597), stamp(-535),
            "COMPLETED", 0.5, "FIXED_TEMPLATE", "DELIVERABLE", None,
        ),
    )
    conn.execute("INSERT INTO generation_variant VALUES (?,?)", ("variant1", "task1"))
    conn.execute(
        "INSERT INTO generation_attempt VALUES (?,?,?,?,?,?,?,?)",
        (
            "attempt1", "variant1", "kling_official_api", "cloud1", "SUCCEEDED",
            stamp(-535), stamp(-500), stamp(-100),
        ),
    )
    conn.execute(
        "INSERT INTO download_artifact VALUES (?,?,?)",
        ("attempt1", "READY", stamp(-85)),
    )
    conn.execute(
        "INSERT INTO multi_cutflow_task VALUES (?,?,?)",
        ("PENDING_REVIEW", stamp(-70), None),
    )
    conn.commit()
    conn.close()
    mtime = path.stat().st_mtime_ns
    report = module.database_report(path, hours=12, cloud_limit=20)
    assert report["stage_timings"]["analysis_queue_wait"]["p50_seconds"] == 3
    assert report["stage_timings"]["analysis_execution"]["p50_seconds"] == 62
    assert report["stage_timings"]["attempt_to_provider_acceptance"]["p50_seconds"] == 35
    assert report["stage_timings"]["provider_terminal_to_local_artifact"]["p50_seconds"] == 15
    assert report["cloud_active_local_records"] == 0
    assert report["cut_multi_statuses_all_time"]["PENDING_REVIEW"] == 1
    assert path.stat().st_mtime_ns == mtime


def test_log_sample_is_aggregate_only(tmp_path: Path) -> None:
    logs = tmp_path / "var" / "classic-lan" / "logs"
    logs.mkdir(parents=True)
    (logs / "api.log").write_text(
        'INFO "GET /api/v1/tasks/12345678-1234-1234-1234-123456789abc HTTP/1.1" 401\n'
        'INFO "GET /api/v1/tasks/12345678-1234-1234-1234-123456789abc HTTP/1.1" 200\n',
        encoding="utf-8",
    )
    (logs / "runner-events.log").write_text("", encoding="utf-8")
    report = module.logs_report(tmp_path, 1_000)
    assert report["request_statuses_in_api_tail"] == {"401": 1, "200": 1}
    assert report["most_requested_routes_in_api_tail"] == {
        "GET /api/v1/tasks/:id 401": 1,
        "GET /api/v1/tasks/:id 200": 1,
    }
    assert "12345678" not in str(report)
