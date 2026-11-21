import pytest
from libtv_agent.domain.models import Base
from libtv_agent.services.database_schema import inspect_database_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_disabled_optional_table_does_not_hide_missing_core_schema():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("DROP TABLE generation_feedback"))
            strict = await connection.run_sync(inspect_database_schema)
            assert not strict.ready
            optional = frozenset({"generation_feedback"})
            compatible = await connection.run_sync(
                lambda conn: inspect_database_schema(conn, optional_tables=optional)
            )
            assert compatible.ready
            await connection.execute(text("DROP TABLE generation_command"))
            outdated = await connection.run_sync(
                lambda conn: inspect_database_schema(conn, optional_tables=optional)
            )
            assert not outdated.ready
            assert outdated.missing_tables == ("generation_command",)
    finally:
        await engine.dispose()
