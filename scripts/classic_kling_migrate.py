"""Explicit additive Kling schema upgrade for classic only; defaults to read-only."""

import argparse
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = (
    "('PREPARED','DISPATCH_PENDING','LAUNCHING','RUNNING',"
    "'RECONCILING','UNKNOWN_AFTER_SUBMIT','SUCCEEDED')"
)


def upgrade_database(path: Path, *, apply: bool = False) -> dict:
    path = path.resolve(strict=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = inspector.get_table_names()
            if "alembic_version" in tables:
                raise RuntimeError("Versioned database: use standard Alembic upgrade")
            columns = {c["name"] for c in inspector.get_columns("generation_attempt")}
            indexes = {i["name"] for i in inspector.get_indexes("generation_attempt")}
            required = {
                "submission_key",
                "provider_account_id",
                "submission_attempt_count",
                "submission_started_at",
                "submission_finished_at",
                "reconciliation_attempt_count",
                "next_reconciliation_at",
                "last_reconciliation_at",
                "last_reconciliation_error",
            }
            if (
                required <= columns
                and "generation_submission_attempt" in tables
                and "uq_attempt_active_variant" in indexes
            ):
                return {"status": "already_current"}
            for key in ("variant_id", "intent_hash"):
                duplicate = connection.execute(
                    text(
                        f"SELECT {key} FROM generation_attempt WHERE status IN {ACTIVE} "
                        f"AND {key} IS NOT NULL GROUP BY {key} HAVING count(*)>1 LIMIT 1"
                    )
                ).first()
                if duplicate:
                    raise RuntimeError(f"Duplicate active {key}; manual inspection required")
        if not apply:
            return {"status": "pending", "changes": ["c2a6f8d1e4b0"], "dataRewrite": False}
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.before-kling-reconciliation-{stamp}.bak")
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        module_path = (
            ROOT / "backend/alembic/versions/c2a6f8d1e4b0_kling_submission_reconciliation.py"
        )
        spec = importlib.util.spec_from_file_location("kling_schema", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
        return {"status": "upgraded", "backup": str(backup), "dataRewrite": False}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = ROOT / "var/classic-lan/classic.db"
    if args.apply:
        from classic_stack import owned_process

        state_path = path.parent / "runtime/stack.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if any(owned_process(p) for p in state.get("processes", [])):
                raise SystemExit("Stop classic via stop-classic-lan.cmd before migration")
    print(json.dumps(upgrade_database(path, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
