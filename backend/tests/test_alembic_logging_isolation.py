"""Alembic in-process schema upgrades must not disable Sol telemetry."""

import asyncio
import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from libtv_agent import config as config_module
from libtv_agent.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_migrating_temporary_schema_does_not_disable_existing_sol_logger(
    tmp_path, monkeypatch,
):
    logger = logging.getLogger("libtv_agent.services.ootd_simple")
    prior_disabled = logger.disabled
    logger.disabled = False
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'migration-lab.db').as_posix()}"
    settings = Settings(app_env="test", database_url=database_url, _env_file=None)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    try:
        await asyncio.to_thread(
            command.upgrade, Config(str(BACKEND_ROOT / "alembic.ini")), "head",
        )
        assert not logger.disabled, "migration must preserve Sol telemetry"
        assert logger.isEnabledFor(logging.INFO) or logger.getEffectiveLevel() > logging.INFO
    finally:
        logger.disabled = prior_disabled
