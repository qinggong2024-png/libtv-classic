"""Read-only, all-brand historic/live metadata collector for OOTD director learning.

No provider calls, no prompt modification, no product-print image/text
retention, no writes to the classic application database. This is NOT an
automatic inference or quality-judgment service: review fields are historical
annotations that still require independent human verification.

The bounded scheduled collector only writes outside production source assets
into a dedicated local evidence directory. No auto-promotion to memory rules.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES

SCHEMA = "allwe-director-memory-shadow-collection-v1"
MAX_SCAN_TASKS = 3000
MAX_STATE_BYTES = 3_000_000


def _json_object(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        result = json.loads(raw or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _valid_db_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(
        ch in "0123456789abcdef" for ch in value
    )


def _digest(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_brand(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    brand = value.strip().lower()
    if not 3 <= len(brand) <= 80:
        return None
    if brand[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        return None
    if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in brand):
        return None
    return brand


def _task_rows(connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    if not 1 <= limit <= MAX_SCAN_TASKS:
        raise ValueError("scan limit is outside safe bounds")
    # Read at most N tasks, independently of the number of product images.
    return connection.execute(
        """SELECT id,created_at,updated_at,site_profile_id,phase,settings_json
           FROM parent_task
           WHERE workflow_kind IS NULL OR workflow_kind != 'DEEP_VIDEO'
           ORDER BY created_at DESC,id DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def _latest_attempt(connection: sqlite3.Connection, variant_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT id,status,provider,provider_model,completed_at,updated_at
           FROM generation_attempt WHERE variant_id = ?
           ORDER BY created_at DESC,id DESC LIMIT 1""",
        (variant_id,),
    ).fetchone()


def _snapshots(connection: sqlite3.Connection, limit: int) -> tuple[dict, ...]:
    observations: list[dict] = []
    for task in _task_rows(connection, limit):
        task_id = task["id"]
        if not _valid_db_id(task_id):
            continue
        settings = _json_object(task["settings_json"])
        # Automatic OOTD only. No custom, unboxing, non-automatic workflows.
        if settings.get("generationMode") != "automatic" or settings.get("videoType") != "ootd":
            continue
        brand = _safe_brand(
            settings.get("directorMemoryBrand") or task["site_profile_id"]
        )
        if not brand:
            # Unknown brand is not reassigned to another brand or inferred.
            brand = "unknown-unassigned"
        variants = connection.execute(
            """SELECT id,prompt_hash,generation_config_json,generation_status,
                      final_approval_status,validation_status,download_status,
                      human_review_seconds,review_notes,created_at
               FROM generation_variant WHERE parent_task_id=?
               ORDER BY variant_index,id""",
            (task_id,),
        ).fetchall()
        for variant in variants:
            if not _valid_db_id(variant["id"]):
                continue
            conf = _json_object(variant["generation_config_json"])
            attempt = _latest_attempt(connection, variant["id"])
            original_roles = conf.get("referenceImageBindings") or ()
            product_count = 0
            storyboard_count = 0
            if isinstance(original_roles, list):
                for item in original_roles[:16]:
                    if not isinstance(item, dict):
                        continue
                    if item.get("role") == "PRODUCT":
                        product_count += 1
                    elif item.get("role") == "STORYBOARD":
                        storyboard_count += 1
            review = variant["review_notes"]
            # Do not copy original prompt, images, SKU, product photo hashes,
            # free-text notes, user identifiers or asset filenames into memory.
            record = {
                "schema": SCHEMA,
                "task_id": task_id,
                "variant_id": variant["id"],
                "task_created_at": task["created_at"],
                "task_updated_at": task["updated_at"],
                "brand_id": brand,
                "director_mode": conf.get("directorMode") or settings.get("directorMode"),
                "planner_profile": conf.get("directorNativeProfile"),
                "duration_seconds": conf.get("duration") or settings.get("durationSeconds"),
                "garment_category": (
                    settings["garmentCategory"]
                    if isinstance(settings.get("garmentCategory"), str)
                    and settings["garmentCategory"] in {
                        "tee", "sweatshirt", "sweater", "hoodie", "scarf", "other"
                    } else None
                ),
                "storyboard_count": storyboard_count,
                "product_image_count": product_count,
                "prompt_sha256": variant["prompt_hash"],
                "task_phase": task["phase"],
                "generation_status": variant["generation_status"],
                "final_approval_status": variant["final_approval_status"],
                "validation_status": variant["validation_status"],
                "download_status": variant["download_status"],
                "human_review_seconds": variant["human_review_seconds"],
                "review_notes_present": bool(str(review or "").strip()),
                "review_notes_sha256": (
                    hashlib.sha256(review.encode("utf-8")).hexdigest()
                    if isinstance(review, str) and review.strip() else None
                ),
                "attempt_id": attempt["id"] if attempt else None,
                "attempt_status": attempt["status"] if attempt else None,
                "provider_model": attempt["provider_model"] if attempt else None,
                "attempt_completed_at": attempt["completed_at"] if attempt else None,
                "collection_mode": "SHADOW_OBSERVE_ONLY",
                "sol_prompt_mutated": False,
                "kling_prompt_mutated": False,
                "automatic_rule_promotion": False,
            }
            observations.append(record)
    return tuple(observations)


def read_snapshots(database: Path, *, limit: int = MAX_SCAN_TASKS) -> tuple[dict, ...]:
    if not database.is_file():
        raise FileNotFoundError("classic SQLite database not found")
    uri = "file:" + database.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        return _snapshots(connection, limit)
    finally:
        connection.close()


def _load_state(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("collection state exceeds max size")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema") != SCHEMA:
        raise ValueError("invalid collection state")
    state = saved.get("snapshots")
    if not isinstance(state, dict) or any(
        not _valid_db_id(k.split(":", 1)[0]) or not isinstance(v, str) or len(v) != 64
        for k, v in state.items()
    ):
        raise ValueError("invalid snapshot checkpoint")
    return state


def _atomic_state(path: Path, state: dict[str, str]) -> None:
    rendered = json.dumps(
        {"schema": SCHEMA, "snapshots": state},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    if len(rendered.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("collection state exceeds max size")
    fd, tmp = tempfile.mkstemp(prefix=".shadow-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _coverage(snapshot: tuple[dict, ...]) -> dict[str, dict[str, int]]:
    brands = {
        brand: {"variants_seen": 0, "approved": 0, "completed": 0}
        for brand in SITE_STYLE_PROFILES
    }
    for entry in snapshot:
        stats = brands.setdefault(entry["brand_id"], {
            "variants_seen": 0, "approved": 0, "completed": 0
        })
        stats["variants_seen"] += 1
        if entry["final_approval_status"] == "APPROVED":
            stats["approved"] += 1
        if entry["attempt_status"] == "SUCCEEDED":
            stats["completed"] += 1
    return brands


def collect_once(database: Path, root: Path, *, limit: int = MAX_SCAN_TASKS,
                 dry_run: bool = False) -> dict:
    """Collect all brands; no write to database. Repeat is idempotent.

    A crash between append and checkpoint may duplicate an event, but its
    event_id is deterministic and subsequent readers can de-duplicate.
    """
    snapshot = read_snapshots(database, limit=limit)
    now = datetime.now(UTC)
    summary = {
        "schema": SCHEMA,
        "mode": "SHADOW_OBSERVE_ONLY",
        "brands": _coverage(snapshot),
        "variants_read": len(snapshot),
        "provider_calls": 0, "cut_calls": 0, "prompt_mutations": 0,
        "db_writes": 0, "automatic_rule_promotions": 0,
    }
    if dry_run:
        return {**summary, "new_events": 0, "dry_run": True}
    root = root.resolve()
    if root == database.resolve().parent or root.is_relative_to(database.resolve().parent):
        # Store under the separate runtime data directory, not alongside DB.
        if root == database.resolve().parent:
            raise ValueError("collection directory cannot equal database directory")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or (root / "state.json").is_symlink():
        raise ValueError("collection symlink is forbidden")
    lock = root / "collector.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {**summary, "new_events": 0, "already_running": True}
    try:
        os.close(fd)
        state_file = root / "state.json"
        previous = _load_state(state_file)
        state = dict(previous)
        to_write = []
        for record in snapshot:
            key = record["task_id"] + ":" + record["variant_id"]
            fingerprint = _digest(record)
            if previous.get(key) == fingerprint:
                continue
            event = {
                **record,
                "event_id": _digest((key, fingerprint)),
                "observation_sha256": fingerprint,
                "observed_at_utc": now.isoformat(timespec="seconds"),
            }
            to_write.append(event)
            state[key] = fingerprint
        event_path = root / f"events-{now:%Y%m%d}.jsonl"
        if to_write:
            with event_path.open("a", encoding="utf-8", newline="\n") as output:
                for event in to_write:
                    output.write(json.dumps(
                        event, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ) + "\n")
                output.flush()
                os.fsync(output.fileno())
        _atomic_state(state_file, state)
        summary_path = root / "latest-summary.json"
        _atomic_json(summary_path, {
            **summary, "new_events": len(to_write),
            "observed_at_utc": now.isoformat(timespec="seconds"),
            "event_file": event_path.name,
        })
        return {**summary, "new_events": len(to_write), "dry_run": False}
    finally:
        lock.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".shadow-summary-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
