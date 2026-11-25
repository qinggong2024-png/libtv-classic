"""Read-only production *schema* parity gate against a fresh migrated test DB.

NEVER copies, edits, queries content rows, or migrates the production database.
Runs Alembic ONLY against a disposable synthetic temporary database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from libtv_agent import config as config_module
from libtv_agent.config import Settings
from libtv_agent.domain.models import Base
from sqlalchemy import create_engine


def _schema(path: Path, *, read_only: bool) -> dict[str, tuple]:
    url = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(url if read_only else str(path), uri=read_only, timeout=3)
    try:
        connection.execute("PRAGMA query_only=ON")
        names = (
            record[0] for record in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return {
            name: tuple(
                (item[1], item[2], item[3], item[5])
                for item in connection.execute(
                    'PRAGMA table_info("' + name.replace('"', '""') + '")'
                )
            )
            for name in names
        }
    finally:
        connection.close()


def compare_schema(
    project: Path, production: Path, *, schema_source: str = "classic",
) -> dict:
    """Return schema metadata only; deliberately never SELECT business rows."""
    if not production.is_file():
        raise FileNotFoundError("production DB is not a regular file")
    with tempfile.TemporaryDirectory(prefix="allwe-v2-schema-migration-lab-") as temp:
        synthetic = Path(temp) / "synthetic-migrated.db"
        if schema_source == "classic":
            # ALLWE classic 5176 uses init_offline_db.py / metadata.create_all
            # for fresh installation, NOT the normal Alembic startup script.
            # Recreate that exact schema shape in a disposable local DB.
            engine = create_engine("sqlite:///" + synthetic.as_posix())
            try:
                Base.metadata.create_all(engine)
            finally:
                engine.dispose()
        elif schema_source == "migrated":
            settings = Settings(
                app_env="test",
                database_url="sqlite+aiosqlite:///" + synthetic.as_posix(),
                _env_file=None,
            )
            config_module.get_settings = lambda: settings
            command.upgrade(Config(str(project / "backend" / "alembic.ini")), "head")
        else:
            raise ValueError("invalid schema source")
        expected = _schema(synthetic, read_only=False)
        actual = _schema(production, read_only=True)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    different_columns = sorted(
        name for name in expected.keys() & actual.keys()
        if expected[name] != actual[name]
    )
    differences = {}
    for name in different_columns:
        expected_fields = {field[0]: field[1:] for field in expected[name]}
        actual_fields = {field[0]: field[1:] for field in actual[name]}
        differences[name] = {
            "missing_columns": sorted(set(expected_fields) - set(actual_fields)),
            "extra_columns": sorted(set(actual_fields) - set(expected_fields)),
            "different_signatures": sorted(
                field for field in expected_fields.keys() & actual_fields.keys()
                if expected_fields[field] != actual_fields[field]
            ),
            "different_signature_details": {
                field: {
                    "expected_type_notnull_pk": expected_fields[field],
                    "production_type_notnull_pk": actual_fields[field],
                }
                for field in sorted(expected_fields.keys() & actual_fields.keys())
                if expected_fields[field] != actual_fields[field]
            },
        }
    return {
        "schema_source": schema_source,
        "scope": "schema-only / zero production business rows read",
        "expected_tables": len(expected),
        "production_tables": len(actual),
        "missing_tables": missing,
        "extra_tables": extra,
        "column_mismatch_tables": different_columns,
        "schema_differences": differences,
        "functional_columns_match": not (
            [name for name in missing if name != "alembic_version"]
            or extra
            or any(
                detail["missing_columns"] or detail["extra_columns"]
                for detail in differences.values()
            )
        ),
        "schema_equal": not (missing or extra or different_columns),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--production-db", type=Path, required=True)
    parser.add_argument(
        "--schema-source", choices=["classic", "migrated"], default="classic",
        help="compare the real classic 5176 create_all schema by default",
    )
    args = parser.parse_args()
    result = compare_schema(
        args.project_root, args.production_db, schema_source=args.schema_source,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["schema_equal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
