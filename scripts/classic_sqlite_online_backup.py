"""Private, consistent online SQLite snapshot for ALLWE Classic release rehearsals.

Reads the live DB through SQLite's official online backup API. It never
restores or modifies the source DB and never sends requests to any provider.
The target must be a fresh directory inside a private runtime backup root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

TABLES = ("parent_task", "generation_attempt", "generation_command", "multi_cutflow_task")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_online_backup(
    source_path: Path,
    private_backup_root: Path,
    *,
    deadline_seconds: float = 35,
) -> dict[str, object]:
    source = source_path.resolve(strict=True)
    backup_root = private_backup_root.resolve(strict=True)
    if not source.is_file() or source.suffix != ".db":
        raise ValueError("Classic SQLite source must be a .db file")
    if backup_root == source.parent or source.is_relative_to(backup_root):
        raise ValueError("Backup target must be a separate protected directory")
    if source.parent.name == "classic-lan" and not backup_root.is_relative_to(
        source.parent / "runtime"
    ):
        raise ValueError("Production Classic backups must stay under its private runtime")
    created = datetime.now(UTC)
    name = created.strftime("classic-%Y%m%dT%H%M%S") + f"-{time.monotonic_ns() % 1_000_000_000:09d}"
    directory = backup_root / name
    directory.mkdir(mode=0o700, exist_ok=False)
    snapshot = directory / "classic.db"
    started = time.monotonic()

    try:
        # Back up *via* SQLite rather than filesystem-copying a live journal.
        with closing(
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=3)
        ) as original:
            original.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(snapshot, timeout=3)) as destination:

                def progress(_status: int, _remaining: int, _total: int) -> None:
                    if time.monotonic() - started > deadline_seconds:
                        raise TimeoutError("SQLite backup exceeded safe deadline")

                original.backup(destination, pages=256, progress=progress, sleep=0.05)
                destination.commit()

        # A restore drill does not use or touch the production SQLite file.
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=2)) as clone:
            clone.execute("PRAGMA query_only=ON")
            integrity = clone.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("SQLite backup integrity_check failed")
            tables = {
                row[0] for row in clone.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not set(TABLES[:3]).issubset(tables):
                raise RuntimeError("SQLite backup missing Classic task tables")
            counts = {
                name: int(clone.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in TABLES
                if name in tables
            }
        report: dict[str, object] = {
            "kind": "classic-sqlite-online-backup",
            "createdAtUtc": created.isoformat(),
            "integrity": "ok",
            "file": "classic.db",
            "sizeBytes": snapshot.stat().st_size,
            "sha256": sha256(snapshot),
            "counts": counts,
            "restoredCloneReadable": True,
            "sourceUnmodified": True,
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }
        (directory / "manifest.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"backupDirectory": str(directory), **report}
    except BaseException:
        # A failed backup must never look like a valid restoration source.
        snapshot.unlink(missing_ok=True)
        (directory / "manifest.json").unlink(missing_ok=True)
        directory.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--private-backup-root", type=Path, required=True)
    args = parser.parse_args()
    result = make_online_backup(args.database, args.private_backup_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
