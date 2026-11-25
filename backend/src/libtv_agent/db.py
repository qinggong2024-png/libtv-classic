import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from libtv_agent.services.performance_observation import install_database_observation


class Database:
    def __init__(self, url: str) -> None:
        # Short concurrent writes should wait for SQLite's writer rather than fail
        # immediately; external AI calls must still run outside all DB transactions.
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True,
            **({"connect_args": {"timeout": 10.0}} if url.startswith("sqlite") else {}),
        )
        if self.engine.dialect.name == "sqlite":
            # SQLite does not provide the SQL `now()` function used by the
            # model server defaults. Register it on every pooled connection.
            @event.listens_for(self.engine.sync_engine, "connect")
            def register_sqlite_functions(dbapi_connection, _connection_record) -> None:
                dbapi_connection.create_function(
                    "now",
                    0,
                    lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                )

        install_database_observation(self.engine.sync_engine)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        # File delivery must not wait behind a paid-task write for ten seconds.
        # Use a separately bounded, unpooled connection for OPTIONAL naming
        # and analytics only. Never route business/paid transactions here.
        self.download_metadata_engine = self.engine
        if (self.engine.dialect.name == "sqlite" and self.engine.url.database
                and self.engine.url.database != ":memory:"):
            self.download_metadata_engine = create_async_engine(
                self.engine.url, poolclass=NullPool, connect_args={"timeout": 0.25},
            )
            event.listen(self.download_metadata_engine.sync_engine, "connect",
                         register_sqlite_functions)
            install_database_observation(self.download_metadata_engine.sync_engine)
        self.download_metadata_session_factory = async_sessionmaker(
            self.download_metadata_engine, expire_on_commit=False,
        )

    async def sqlite_write_ready(self) -> bool | None:
        """Bounded writer reservation; no row writes or journal-mode changes."""
        url = self.engine.url
        if self.engine.dialect.name != "sqlite" or not url.database or url.database == ":memory:":
            return None
        if url.database.startswith("file:"):
            return None  # URI-backed databases require an explicit probe contract.
        def probe() -> bool:
            try:
                path = Path(url.database).resolve()
                connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.rollback()
                    return True
                finally:
                    connection.close()
            except sqlite3.Error:
                return False

        return await asyncio.to_thread(probe)

    async def dispose(self) -> None:
        if self.download_metadata_engine is not self.engine:
            await self.download_metadata_engine.dispose()
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session_factory() as session:
        yield session
