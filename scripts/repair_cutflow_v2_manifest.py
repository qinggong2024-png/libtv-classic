"""Repair a pre-admission v2 package that predates manifest.idempotencyKey."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from libtv_agent.services.cutflow_handoff import safe_path, write_json_atomic  # noqa: E402
from libtv_agent.services.cutflow_multi_adapter import multi_idempotency_key  # noqa: E402

CONTRACT = "libtv-cut-lan-multi/2"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--storage", default=str(ROOT / "var" / "classic-lan"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    task_id = str(uuid.UUID(args.task_id))
    storage = Path(args.storage)
    config = json.loads(
        (storage / "runtime" / "cutflow-config.json").read_text(encoding="utf-8")
    )
    root = Path(config["handoffRoot"])
    package = safe_path(root, f"inbox/multi-{task_id}")
    manifest_path = safe_path(package, "manifest.json")
    status_path = safe_path(root, f"outbox/{task_id}/status.json")
    db_path = storage / "classic.db"

    with sqlite3.connect(db_path) as database:
        database.row_factory = sqlite3.Row
        row = database.execute(
            "select id,status,manifest_path from multi_cutflow_task where id=?",
            (task_id.replace("-", ""),),
        ).fetchone()
    if row is None:
        raise SystemExit("task not found")
    if row["status"] != "READY":
        raise SystemExit(f"task is not pre-admission READY: {row['status']}")
    if status_path.exists():
        raise SystemExit("refusing repair: CUT already wrote outbox status")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contractVersion") != CONTRACT:
        raise SystemExit("task is not a v2 package")
    idempotency_key = multi_idempotency_key(task_id)
    old_raw = manifest_path.read_bytes()
    old_hash = sha256_bytes(old_raw)
    repaired = {**manifest, "idempotencyKey": idempotency_key}
    new_raw = json.dumps(
        repaired,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    new_hash = sha256_bytes(new_raw)
    print(json.dumps({
        "taskId": task_id,
        "oldManifestSha256": old_hash,
        "newManifestSha256": new_hash,
        "idempotencyKey": idempotency_key,
        "apply": args.apply,
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    audit_dir = storage / "runtime" / "cutflow-v2-repair"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "taskId": task_id,
        "repairedAt": datetime.now(UTC).isoformat(),
        "oldManifestSha256": old_hash,
        "newManifestSha256": new_hash,
        "idempotencyKey": idempotency_key,
        "reason": "pre-admission package lacked manifest.idempotencyKey",
    }
    (audit_dir / f"{task_id}.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audit_dir / f"{task_id}.before-manifest.json").write_bytes(old_raw)

    temporary = manifest_path.with_name(f".manifest.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(new_raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
        write_json_atomic(package / "READY", {"sha256": new_hash})
    finally:
        temporary.unlink(missing_ok=True)

    with sqlite3.connect(db_path) as database:
        database.row_factory = sqlite3.Row
        database.execute("begin immediate")
        row = database.execute(
            "select manifest_json from multi_cutflow_task "
            "where id=? and status='READY'",
            (task_id.replace("-", ""),),
        ).fetchone()
        if row is None:
            database.rollback()
            raise SystemExit("task changed during repair")
        descriptor = json.loads(row["manifest_json"])
        descriptor["idempotencyKey"] = idempotency_key
        descriptor["manifestSha256"] = new_hash
        descriptor["manifestRelativePath"] = f"inbox/multi-{task_id}/manifest.json"
        database.execute(
            "update multi_cutflow_task set manifest_json=?, updated_at=CURRENT_TIMESTAMP "
            "where id=? and status='READY'",
            (json.dumps(descriptor, ensure_ascii=False), task_id.replace("-", "")),
        )
        database.commit()
    print("REPAIRED")


if __name__ == "__main__":
    main()
