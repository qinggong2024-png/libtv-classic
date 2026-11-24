"""Idle polls must not reserve the SQLite writer or replay paid work."""
import asyncio
import sqlite3

import pytest
from libtv_agent.db import Database
from libtv_agent.domain.models import Base
from libtv_agent.services.durable_analysis import claim_next
from libtv_agent.services.durable_confirmation import claim_confirmation
from sqlalchemy import event


@pytest.mark.asyncio
async def test_empty_queues_do_not_write_under_competing_writer(tmp_path):
    path = tmp_path / "idle.db"
    database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    statements = []
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Warm the pool before the external writer takes its lock. Test-only
        # journal pragmas on new connections must not affect this check.
        async with database.session_factory() as session:
            await session.connection()
        @event.listens_for(database.engine.sync_engine, "before_cursor_execute")
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().split(None, 1)[0].upper())
        writer = sqlite3.connect(path, timeout=1)
        try:
            writer.execute("BEGIN IMMEDIATE")
            async def poll():
                async with database.session_factory() as session:
                    assert await claim_next(session, stale_seconds=660) is None
                    assert not session.in_transaction()
                    assert await claim_confirmation(session) is None
                    assert not session.in_transaction()
            await asyncio.wait_for(poll(), timeout=2)
            assert statements == ["SELECT", "SELECT"]
        finally:
            writer.rollback()
            writer.close()
    finally:
        await database.dispose()
