"""Apply only the new output-view table on an existing unversioned Classic SQLite DB.

Run only while the independent Classic API/runner are stopped, after a verified
SQLite backup. Never execute all Alembic revisions against a live unversioned DB.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from libtv_agent.domain.models import CutflowOutputView
from sqlalchemy import create_engine, inspect


def migrate(database_path: Path) -> None:
    if not database_path.is_file():
        raise SystemExit("Refusing to create a new database; target must exist")
    engine = create_engine(f"sqlite:///{database_path.resolve().as_posix()}", connect_args={"timeout": 10})
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            existing = set(inspector.get_table_names())
            if not {"app_user", "multi_cutflow_task"}.issubset(existing):
                raise SystemExit("Classic schema prerequisites are missing")
            target = CutflowOutputView.__table__
            if target.name in existing:
                columns = {col["name"] for col in inspector.get_columns(target.name)}
                if columns != {"user_id", "batch_id", "output_id", "viewed_at"}:
                    raise SystemExit("Existing output view table has an unexpected schema")
            else:
                target.create(connection, checkfirst=False)
            inspector = inspect(connection)
            assert target.name in inspector.get_table_names()
            assert {col["name"] for col in inspector.get_columns(target.name)} == {
                "user_id", "batch_id", "output_id", "viewed_at",
            }
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_path", type=Path)
    migrate(parser.parse_args().database_path)
    print("Classic CUT output-view table ready; no existing task data modified")
