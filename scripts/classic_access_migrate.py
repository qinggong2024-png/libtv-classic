"""Explicit, backed-up account-schema upgrade for the classic metadata-created SQLite DB.

Default: report pending schema only. --apply requires the classic site to be stopped.
Never reads credentials, changes passwords, or rewrites existing account assignments.
"""

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))


def upgrade_database(path: Path, *, apply: bool = False) -> dict:
    path = path.resolve(strict=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = {c["name"] for c in inspector.get_columns("app_user")}
            migrations = []
            if "brand_id" in columns:
                required = {
                    "is_brand_admin",
                    "daily_budget_microusd",
                    "image_price_microusd",
                    "video_price_microusd",
                }
                if not required.issubset(columns) or not {"brand", "account_usage"}.issubset(
                    inspector.get_table_names()
                ):
                    raise RuntimeError("Partial account schema detected; inspect before applying")
                added = {
                    "page_permissions_json", "depth_video_price_microusd",
                    "recreate_video_price_microusd",
                }
                usage_columns = {c["name"] for c in inspector.get_columns("account_usage")}
                complete = added.issubset(columns) and "pricing_category" in usage_columns
                if complete:
                    return {"status": "already_current"}
                if added.intersection(columns) or "pricing_category" in usage_columns:
                    raise RuntimeError(
                        "Partial page-access schema detected; inspect before applying"
                    )
            else:
                migrations.append("b41e52d6a710_brand_account_budgets.py")
            migrations.append("f6a12c9e408b_page_access_deep_video_rates.py")
            if "alembic_version" in inspector.get_table_names():
                raise RuntimeError("Use the standard Alembic upgrade for a versioned database")
        if not apply:
            return {"status": "pending", "changes": migrations}
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.before-account-access-{stamp}.bak")
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        with engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Operations.context(MigrationContext.configure(connection)):
                for filename in migrations:
                    spec = importlib.util.spec_from_file_location(
                        "account_migration", ROOT / "backend" / "alembic" / "versions" / filename
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.upgrade()
        return {"status": "upgraded", "backup": str(backup)}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = ROOT / "var" / "classic-lan" / "classic.db"
    if args.apply:
        from classic_stack import owned_process

        state_file = path.parent / "runtime" / "stack.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if any(owned_process(p) for p in state.get("processes", [])):
                raise SystemExit("Stop the independent classic site before applying the migration")
    print(json.dumps(upgrade_database(path, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
