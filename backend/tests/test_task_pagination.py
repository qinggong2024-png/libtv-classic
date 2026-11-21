import uuid
from datetime import datetime

import pytest
from libtv_agent.db import Database
from libtv_agent.domain.models import Base, ParentTask, User
from libtv_agent.services.tasks import list_tasks
from sqlalchemy import text


@pytest.mark.parametrize("page_size", [1, 2])
@pytest.mark.parametrize(
    "timestamps",
    [
        ["2026-10-16 01:00:00.900000", "2026-10-16 01:00:00.800000", "2026-10-16 01:00:00.700000"],
        ["2026-10-16 01:00:00.999999", "2026-10-16 01:00:00.999998", "2026-10-16 01:00:00.999997"],
        ["2026-10-16 01:00:00", "2026-10-16 01:00:00.000000", "2026-10-16 01:00:00"],
        ["2026-10-16 01:00:01", "2026-10-16 01:00:00.999999", "2026-10-16 01:00:00"],
    ],
)
async def test_sqlite_cursor_keeps_microseconds_and_default_timestamp_ties(timestamps, page_size):
    database = Database("sqlite+aiosqlite:///:memory:")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        owner = uuid.uuid4()
        identities = [uuid.UUID(int=index) for index in range(1, 4)]
        async with database.session_factory() as session:
            session.add(User(id=owner, username="pagination-test", password_hash="disabled"))
            await session.flush()
            for identity in identities:
                session.add(
                    ParentTask(
                        id=identity,
                        user_id=owner,
                        client_task_id=str(identity),
                        title=str(identity),
                        asset_count=0,
                    )
                )
            await session.flush()
            for identity, timestamp in zip(identities, timestamps, strict=True):
                # Exercise both real SQLite storage forms, not just ORM binds.
                await session.execute(
                    text("UPDATE parent_task SET updated_at=:stamp WHERE id=:identity"),
                    {"stamp": timestamp, "identity": identity.hex},
                )
            await session.commit()
        expected = sorted(
            identities,
            key=lambda identity: (
                datetime.fromisoformat(timestamps[identities.index(identity)]),
                identity,
            ),
            reverse=True,
        )
        seen = []
        cursor_time = cursor_id = None
        async with database.session_factory() as session:
            for _ in range(len(identities) + 1):
                page = await list_tasks(
                    session,
                    owner,
                    limit=page_size,
                    cursor_updated_at=cursor_time,
                    cursor_id=cursor_id,
                )
                if not page:
                    break
                seen.extend(task.id for task in page)
                cursor_time, cursor_id = page[-1].updated_at, page[-1].id
        assert seen == expected
    finally:
        await database.dispose()
