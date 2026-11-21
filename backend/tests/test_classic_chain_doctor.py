import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from libtv_agent.config import Settings

spec = importlib.util.spec_from_file_location(
    "classic_chain_doctor",
    Path(__file__).resolve().parents[2] / "scripts/classic_chain_doctor.py",
)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)
NOW = datetime(2026, 11, 7, 8, tzinfo=UTC)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "classic.db"
    with sqlite3.connect(path) as database:
        database.executescript("""
            CREATE TABLE runner_host(status TEXT,last_heartbeat_at TEXT);
            CREATE TABLE parent_task(id TEXT,phase TEXT,updated_at TEXT);
            CREATE TABLE generation_variant(id TEXT,parent_task_id TEXT,download_status TEXT);
            CREATE TABLE generation_attempt(
                id TEXT,variant_id TEXT,status TEXT,provider TEXT,task_id TEXT,
                last_polled_at TEXT,submitted_at TEXT,created_at TEXT,updated_at TEXT,
                last_error_code TEXT,raw_response_json TEXT);
            CREATE TABLE generation_command(status TEXT,updated_at TEXT);
            CREATE TABLE api_idempotency_record(
                response_status INTEGER,created_at TEXT,idempotency_key TEXT);
            CREATE TABLE image_generation_item(status TEXT,updated_at TEXT);
            CREATE TABLE cutflow_handoff(status TEXT,updated_at TEXT);
        """)
        database.execute("INSERT INTO runner_host VALUES('ONLINE',?)", (NOW.isoformat(),))
    return path


def insert_attempt(
    database,
    *,
    identifier,
    variant="v",
    status,
    handle="paid-original",
    error=None,
    timestamp="2026-11-07T07:59:50+00:00",
):
    database.execute(
        "INSERT INTO generation_attempt VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            variant,
            status,
            "kling_official_cli",
            handle,
            timestamp,
            timestamp,
            timestamp,
            timestamp,
            error,
            '{"secret":"sensitive-marker"}',
        ),
    )


def test_missing_database_is_reported_without_creating_it(tmp_path):
    path = tmp_path / "absent.db"
    report = doctor.diagnose(path, now=NOW)
    assert report["status"] == "ERROR"
    assert report["errorCode"] == "CLASSIC_DATABASE_UNREADABLE_OR_SCHEMA_INVALID"
    assert not path.exists()


def test_healthy_database_ignores_historical_failure_and_is_read_only(database_path):
    with sqlite3.connect(database_path) as database:
        database.execute("INSERT INTO parent_task VALUES('p','DELIVERABLE',?)", (NOW.isoformat(),))
        database.execute("INSERT INTO generation_variant VALUES('v','p','READY')")
        insert_attempt(
            database,
            identifier="old",
            status="FAILED_TERMINAL",
            timestamp="2026-11-01T00:00:00+00:00",
        )
        insert_attempt(database, identifier="new", status="SUCCEEDED")
    before = hashlib.sha256(database_path.read_bytes()).digest()
    report = doctor.diagnose(database_path, now=NOW)
    assert report["status"] == "OK"
    assert report["readOnly"] is True
    assert all(check["count"] == 0 for check in report["checks"])
    assert "sensitive-marker" not in json.dumps(report)
    assert hashlib.sha256(database_path.read_bytes()).digest() == before


def test_stale_work_is_distinguished_from_uncertain_paid_history(database_path):
    old = "2026-11-01T00:00:00+00:00"
    with sqlite3.connect(database_path) as database:
        database.execute("UPDATE runner_host SET last_heartbeat_at=?", (old,))
        database.execute("INSERT INTO parent_task VALUES('p','ACTION_REQUIRED',?)", (old,))
        database.execute("INSERT INTO generation_variant VALUES('v','p','FAILED')")
        insert_attempt(
            database,
            identifier="delivery",
            status="SUCCEEDED",
            error="DOWNLOAD_RETRYABLE",
            timestamp=old,
        )
        insert_attempt(
            database,
            identifier="missing",
            variant="missing",
            status="RUNNING",
            handle=None,
            timestamp=old,
        )
        insert_attempt(
            database,
            identifier="unknown",
            variant="unknown",
            status="UNKNOWN_AFTER_SUBMIT",
            handle=None,
            timestamp=old,
        )
        database.execute(
            "INSERT INTO api_idempotency_record VALUES(0,?,'sensitive-marker')", (old,)
        )
        database.execute("INSERT INTO image_generation_item VALUES('RUNNING',?)", (old,))
        database.execute("INSERT INTO cutflow_handoff VALUES('RUNNING',?)", (old,))
    report = doctor.diagnose(database_path, now=NOW)
    checks = {check["code"]: check for check in report["checks"]}
    assert report["status"] == "ERROR"
    assert checks["RUNNING_HANDLE_MISSING"]["severity"] == "ERROR"
    assert checks["PAID_SUBMISSION_UNCERTAIN"]["severity"] == "WARNING"
    for code in (
        "RUNNER_HEARTBEAT_STALE",
        "DELIVERY_RETRY_PENDING",
        "PAID_SUBMISSION_UNCERTAIN",
        "IDEMPOTENCY_CLAIM_STALE",
        "IMAGE_JOB_STALE",
        "CUT_HANDOFF_STALE",
    ):
        assert checks[code]["count"] == 1
    assert "sensitive-marker" not in json.dumps(report)


def test_cli_uses_meaningful_exit_status_and_safe_json(database_path, capsys):
    # The fixture heartbeat can be old by CLI wall clock; this is safely an error.
    assert doctor.main(["--database", str(database_path)]) in (0, 2)
    report = json.loads(capsys.readouterr().out)
    assert report["readOnly"] is True


def test_regression_gate_validates_safe_environment_and_propagates_failures(
    tmp_path,
    monkeypatch,
):
    gate_spec = importlib.util.spec_from_file_location(
        "verify_classic_chain",
        Path(__file__).resolve().parents[2] / "scripts/verify_classic_chain.py",
    )
    gate = importlib.util.module_from_spec(gate_spec)
    gate_spec.loader.exec_module(gate)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///do-not-open-production.db")
    monkeypatch.setenv("PAID_GENERATION_ENABLED", "true")
    monkeypatch.setenv("G1_TEST_POSTGRES_URL", "do-not-use-inherited-database")
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == 3
    args, options = captured[0]
    env = options["env"]
    settings = Settings(
        _env_file=None,
        app_env=env["APP_ENV"],
        database_url=env["DATABASE_URL"],
        libtv_adapter_mode=env["LIBTV_ADAPTER_MODE"],
        libtv_execution_mode=env["LIBTV_EXECUTION_MODE"],
        paid_generation_enabled=env["PAID_GENERATION_ENABLED"],
        paid_generation_confirmation=env["PAID_GENERATION_CONFIRMATION"],
    )
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.libtv_adapter_mode == "fake"
    assert settings.libtv_execution_mode == "dry-run"
    assert not settings.paid_generation_enabled
    assert not settings.paid_generation_confirmation
    assert "G1_TEST_POSTGRES_URL" not in env
    assert options["cwd"] == tmp_path
    assert "--junitxml" in args
