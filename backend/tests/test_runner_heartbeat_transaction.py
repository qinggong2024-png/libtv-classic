"""The LAN API must see heartbeat commits before provider work blocks."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import Base, RunnerHost
from libtv_agent.runner import service_runtime
from sqlalchemy import select, update


async def test_heartbeat_is_visible_and_other_writer_is_free_during_provider_work(
    tmp_path,
    monkeypatch,
):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}",
        runner_root=tmp_path / "runner",
    )
    database = Database(settings.database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    stopping = Event()
    observed = []

    async def provider_work(*args, **kwargs):
        # A separate API transaction runs while the runner is in external work.
        try:
            async with database.session_factory() as other:
                host = await other.scalar(select(RunnerHost))
                assert host is not None, "runner heartbeat must already be committed"
                await asyncio.wait_for(
                    other.execute(
                        update(RunnerHost)
                        .where(RunnerHost.id == host.id)
                        .values(protocol_version=2)
                    ),
                    1,
                )
                await other.commit()
                observed.append(host.id)
        finally:
            stopping.set()

    manager = SimpleNamespace(
        ensure_layout=lambda: None,
        find_orphan_processes=lambda: [],
        disk_status=lambda *args: {"status": "OK", "freePercent": 90},
    )
    supervisor = SimpleNamespace(
        run_ready=AsyncMock(return_value=0),
        recover_stale_launch_started_commands=AsyncMock(return_value=0),
        reconcile_completed_parent_phases=AsyncMock(return_value=0),
    )
    monkeypatch.setattr(service_runtime, "Settings", lambda: settings)
    monkeypatch.setattr(service_runtime, "Database", lambda _: database)
    monkeypatch.setattr(service_runtime, "CommandHostManager", lambda *a, **k: manager)
    monkeypatch.setattr(service_runtime, "RunnerSupervisor", lambda *a, **k: supervisor)
    for name in ("create_libtv_adapter", "create_kling_adapter", "create_object_store"):
        monkeypatch.setattr(service_runtime, name, lambda _: object())
    for name in ("_load_video_provider_admin_configs", "_video_provider_admin_config_signatures"):
        monkeypatch.setattr(service_runtime, name, lambda _: (None, None, None))
    for name in (
        "_refresh_libtv_auth_state",
        "get_runtime_config",
        "record_operational_sample",
        "prune_operational_samples",
    ):
        monkeypatch.setattr(service_runtime, name, AsyncMock(return_value=None))
    for name in ("operational_backlog_count", "release_stale_idempotency_claims"):
        monkeypatch.setattr(service_runtime, name, AsyncMock(return_value=0))
    for name in ("recover_stale_confirmation_claims", "recover_stale_batch_confirmation_claims"):
        monkeypatch.setattr(service_runtime, name, AsyncMock(return_value={}))
    monkeypatch.setattr(service_runtime, "auto_confirm_pass_tasks", provider_work)
    # run_service_loop intentionally changes cwd; pytest restores it after this test.
    monkeypatch.chdir(tmp_path)
    await service_runtime.run_service_loop(stopping, tmp_path)
    assert len(observed) == 1
