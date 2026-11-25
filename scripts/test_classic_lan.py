import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classic_lan  # noqa: E402
import classic_runner  # noqa: E402


@pytest.fixture
def isolated_lan(tmp_path, monkeypatch):
    storage = tmp_path / "classic-lan"
    runtime = storage / "runtime"
    runtime.mkdir(parents=True)
    secret = runtime / "session-secret.txt"
    secret.write_text("independent-local-session-secret-for-tests", encoding="utf-8")
    monkeypatch.setattr(classic_lan, "STORAGE", storage)
    monkeypatch.setattr(classic_lan, "SECRET_FILE", secret)
    monkeypatch.setattr(classic_lan, "STATE", runtime / "stack.json")
    monkeypatch.setattr(classic_lan, "protect_runtime", lambda: None)
    return storage


def test_lan_environment_keeps_credentials_and_database_separate(tmp_path, monkeypatch):
    storage = tmp_path / "classic-lan"
    storage.mkdir()
    secret = storage / "session-secret.txt"
    secret.write_text("independent-local-session-secret-for-tests", encoding="utf-8")
    monkeypatch.setattr(classic_lan, "STORAGE", storage)
    monkeypatch.setattr(classic_lan, "SECRET_FILE", secret)
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-key-must-not-escape")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unrelated-production")
    env = classic_lan.lan_environment(8003, 5177, "10.12.34.56")
    assert "OPENAI_API_KEY" not in env
    assert env["DATABASE_URL"].endswith("classic-lan/classic.db")
    assert env["SHARED_AUTHENTICATION_ENABLED"] == "true"
    assert env["APP_ENV"] == "development"
    assert env["AI_ADAPTER_MODE"] == "openai"
    assert env["OPENAI_MODEL"] == "gpt-6.2-sol"
    assert env["AI_CREATIVE_V25_ENABLED"] == "false"
    assert env["CLASSIC_THROUGHPUT_S1S3_ENABLED"] == "true"
    assert env["DOWNLOAD_CONCURRENCY"] == "2"
    assert env["CLASSIC_AUTO_PLAN_CONCURRENCY"] == "3"
    assert env["KLING_API_MAX_ACTIVE_CLOUD_TASKS"] == "20"
    assert "DEPLOYMENT_SOURCE_REVISION" not in env
    assert env["SESSION_COOKIE_NAME"] == "libtv_classic_lan_session"
    assert env["CSRF_COOKIE_NAME"] == "libtv_classic_lan_csrf"
    assert env["PAID_GENERATION_ENABLED"] == "false"
    assert env["LIBTV_EXECUTION_MODE"] == "dry-run"
    assert env["WAVESPEED_ENABLED"] == "false"
    assert "http://10.12.34.56:5177" in env["APP_PUBLIC_ORIGINS"]
    assert str(storage) in env["ADMIN_CREDENTIALS_FILE"]


def test_generation_opt_in_survives_restart_without_inheriting_secrets(isolated_lan, monkeypatch):
    monkeypatch.setenv("PAID_GENERATION_ENABLED", "true")
    monkeypatch.setenv("PAID_GENERATION_CONFIRMATION", "I_ACCEPT_LIBTV_POINT_CHARGES")
    monkeypatch.setenv("KLING_API_KEY", "unrelated-provider-key")
    assert not classic_lan.generation_authorized()
    assert classic_lan.lan_environment(8003, 5177, "10.12.34.56")[
        "PAID_GENERATION_ENABLED"
    ] == "false"
    classic_lan.configure_generation(True, {})
    env = classic_lan.lan_environment(8003, 5177, "10.12.34.56")
    assert classic_lan.generation_authorized()
    assert env["LIBTV_EXECUTION_MODE"] == "live"
    assert env["PAID_GENERATION_ENABLED"] == "true"
    assert env["PAID_GENERATION_CONFIRMATION"] == "I_ACCEPT_LIBTV_POINT_CHARGES"
    assert env["AUTO_RUN_FAKE_COMMANDS"] == "false"
    assert "KLING_API_KEY" not in env
    assert env["LIBTV_ADAPTER_MODE"] == "fake"
    assert env["KLING_API_ENABLED"] == "false"
    assert env["WAVESPEED_ENABLED"] == "false"
    assert str(isolated_lan) in env["RUNNER_ROOT"]
    classic_lan.configure_generation(False, {})
    assert not classic_lan.generation_authorized()
    assert classic_lan.lan_environment(8003, 5177, "10.12.34.56")[
        "LIBTV_EXECUTION_MODE"
    ] == "dry-run"


@pytest.mark.parametrize(
    "policy",
    ["invalid-json", "[]", '{}', '{"version":1,"paidGenerationAuthorized":"true"}'],
)
def test_invalid_generation_policy_fails_closed(isolated_lan, policy):
    (isolated_lan / "runtime" / "generation-policy.json").write_text(policy, encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid classic generation policy"):
        classic_lan.lan_environment(8003, 5177, "10.12.34.56")


def test_policy_cannot_change_under_a_running_process(isolated_lan, monkeypatch):
    monkeypatch.setattr(classic_lan, "owned_process", lambda _: True)
    with pytest.raises(SystemExit, match="Stop the classic"):
        classic_lan.configure_generation(True, {"processes": [{"name": "api"}]})
    assert not (isolated_lan / "runtime" / "generation-policy.json").exists()


def test_runner_readiness_requires_current_process_and_fresh_heartbeat(isolated_lan, monkeypatch):
    monkeypatch.setattr(classic_lan, "owned_process", lambda _: True)
    runner_dir = isolated_lan / "runner"
    runner_dir.mkdir()
    (runner_dir / "service-instance-id").write_text("classic-only", encoding="ascii")
    now = time.time()
    state = {"processes": [{"name": "runner", "created": now - 2}]}
    with sqlite3.connect(isolated_lan / "classic.db") as database:
        database.execute(
            "CREATE TABLE runner_host (service_instance_id TEXT, last_heartbeat_at TEXT)"
        )
        database.execute("INSERT INTO runner_host VALUES (?, ?)", ("classic-only", None))
    assert not classic_lan.runner_ready(state)
    for age, expected in [(90, False), (10, False), (1, True)]:
        with sqlite3.connect(isolated_lan / "classic.db") as database:
            database.execute(
                "UPDATE runner_host SET last_heartbeat_at = ?",
                (datetime.fromtimestamp(now - age, UTC).replace(tzinfo=None).isoformat(),),
            )
        assert classic_lan.runner_ready(state) is expected
    monkeypatch.setattr(classic_lan, "owned_process", lambda _: None)
    assert not classic_lan.runner_ready(state)


def test_authorized_start_rejects_web_only_stack(isolated_lan, monkeypatch):
    classic_lan.configure_generation(True, {})
    state = {
        "processes": [{"name": "api"}, {"name": "web"}],
        "webPort": 5176,
        "apiPort": 8002,
        "lanIp": "10.12.34.56",
        "generationAuthorized": True,
    }
    classic_lan.STATE.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(classic_lan, "owned_process", lambda _: True)
    monkeypatch.setattr(classic_lan, "ready", lambda *args: True)
    monkeypatch.setattr(sys, "argv", ["classic_lan.py", "start"])
    with pytest.raises(SystemExit, match="unhealthy"):
        classic_lan.main()


def test_worker_refuses_unauthorized_or_foreign_database(isolated_lan, monkeypatch):
    monkeypatch.setattr(classic_runner, "generation_authorized", classic_lan.generation_authorized)
    with pytest.raises(SystemExit, match="not authorized"):
        classic_runner.main()
    classic_lan.configure_generation(True, {})
    monkeypatch.setenv("DATABASE_URL", "postgresql://unrelated-production")
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        classic_runner.main()


def test_worker_reuses_launcher_environment_and_classic_logs(isolated_lan, monkeypatch):
    from libtv_agent.runner import local_runner

    classic_lan.configure_generation(True, {})
    for key, value in classic_lan.lan_environment(8003, 5177, "10.12.34.56").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(classic_runner, "STORAGE", isolated_lan)
    monkeypatch.setattr(local_runner, "RUNNER_LOG_PATH", Path("unused"))
    called = []
    monkeypatch.setattr(
        local_runner, "main", lambda stopping=None: called.append(local_runner.RUNNER_LOG_PATH),
    )
    classic_runner.main()
    assert called == [isolated_lan / "logs" / "runner-events.log"]


@pytest.mark.asyncio
async def test_real_worker_loop_heartbeats_in_isolated_database(isolated_lan, monkeypatch):
    from threading import Event

    from libtv_agent.config import Settings
    from libtv_agent.db import Database
    from libtv_agent.domain.models import Base, RunnerHost
    from libtv_agent.runner import service_runtime
    from sqlalchemy import select

    classic_lan.configure_generation(True, {})
    for key, value in classic_lan.lan_environment(8003, 5177, "10.12.34.56").items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    database = Database(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    stopping = Event()

    async def observe_cycle(supervisor, session):
        assert supervisor.settings.paid_generation_enabled
        assert supervisor.settings.libtv_execution_mode == "live"
        assert supervisor.settings.runner_root == isolated_lan / "runner"
        assert not supervisor.settings.kling_api_enabled
        assert not supervisor.settings.wavespeed_enabled
        stopping.set()
        return 0

    monkeypatch.setattr(service_runtime.RunnerSupervisor, "run_ready", observe_cycle)
    try:
        await service_runtime.run_service_loop(stopping, classic_lan.ROOT)
        async with database.session_factory() as session:
            host = await session.scalar(select(RunnerHost))
            assert host is not None
            assert host.last_heartbeat_at is not None
            instance = (isolated_lan / "runner" / "service-instance-id").read_text()
            assert host.service_instance_id == instance
    finally:
        await database.dispose()
