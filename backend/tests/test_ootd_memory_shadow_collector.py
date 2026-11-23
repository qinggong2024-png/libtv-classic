"""All-brand collection is passive, local, idempotent and print-agnostic."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from libtv_agent.services.ootd_director_memory import (
    MemoryDestination,
    MemoryKind,
    MemoryRule,
    MemoryState,
    select_memory,
)
from libtv_agent.services.ootd_memory_shadow_collector import (
    collect_once,
    read_snapshots,
)
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES


def _make_db(path: Path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE parent_task (
          id TEXT, created_at TEXT, updated_at TEXT, site_profile_id TEXT,
          phase TEXT, settings_json TEXT, workflow_kind TEXT
        );
        CREATE TABLE generation_variant (
          id TEXT, parent_task_id TEXT, prompt_hash TEXT,
          generation_config_json TEXT, generation_status TEXT,
          final_approval_status TEXT, validation_status TEXT, download_status TEXT,
          human_review_seconds FLOAT,
          review_notes TEXT, created_at TEXT, variant_index INTEGER
        );
        CREATE TABLE generation_attempt (
          id TEXT, variant_id TEXT, status TEXT, provider TEXT,
          provider_model TEXT, completed_at TEXT, updated_at TEXT,
          created_at TEXT
        );
        """
    )
    connection.commit()
    connection.close()


def _task(database: Path, *, brand: str, index: int,
          video_type="ootd", mode="automatic", note=""):
    task = f"{index + 1:032x}"
    variant = f"{index + 1000:032x}"
    attempt = f"{index + 2000:032x}"
    conf = {
        "directorMode": "cross_scene_v2",
        "directorNativeProfile": "allwe-ootd-sol-dynamic-display-v1",
        "referenceImageBindings": [
            {"role": "STORYBOARD", "sha256": "PRIVATE_STORY_HASH", "imageId": "image_1"},
            {"role": "PRODUCT", "sha256": "PRIVATE_PRODUCT_HASH", "imageId": "image_2"},
        ],
        "duration": 10,
    }
    settings = {
        "videoType": video_type,
        "generationMode": mode,
        "directorMode": "cross_scene_v2",
        "creativeBrief": "Do not store the current graphic's printed text.",
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO parent_task VALUES (?,?,?,?,?,?,?)",
            (task, "2026-11-22 08:00:00", "2026-11-22 08:10:00",
             brand, "DELIVERABLE", json.dumps(settings), "FIXED_TEMPLATE"),
        )
        connection.execute(
            "INSERT INTO generation_variant VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (variant, task, "a" * 64, json.dumps(conf),
             "SUCCEEDED", "APPROVED", "PASSED", "READY", 13, note,
             "2026-11-22 08:00:00", 1),
        )
        connection.execute(
            "INSERT INTO generation_attempt VALUES (?,?,?,?,?,?,?,?)",
            (attempt, variant, "SUCCEEDED", "kling-official",
             "kling-3.0-omni", "2026-11-22 08:07:00",
             "2026-11-22 08:07:00", "2026-11-22 08:01:00"),
        )
    return task, variant


@pytest.fixture
def fake(tmp_path):
    database = tmp_path / "classic.db"
    _make_db(database)
    return database, tmp_path / "persistent-output"


def test_all_configured_brands_are_counted_even_without_rules(fake):
    db, output = fake
    expected = set(SITE_STYLE_PROFILES)
    assert len(expected) >= 13
    for n, brand in enumerate(sorted(expected)):
        _task(db, brand=brand, index=n)
    result = collect_once(db, output)
    assert result["variants_read"] == len(expected)
    assert result["new_events"] == len(expected)
    assert set(result["brands"]) >= expected
    assert all(result["brands"][b]["variants_seen"] == 1 for b in expected)
    assert not result["prompt_mutations"]
    assert not result["provider_calls"]
    assert not result["cut_calls"]


def test_collection_does_not_need_whitelist_rules(fake):
    db, output = fake
    _task(db, brand="brand-created-after-deploy", index=1)
    data = collect_once(db, output)
    assert data["brands"]["brand-created-after-deploy"]["variants_seen"] == 1
    # New brands can carry independently reviewed rules later, but none now.
    rule = MemoryRule(
        rule_id="newbrand.ugc", revision=1, state=MemoryState.ACTIVE,
        kind=MemoryKind.BRAND, destination=MemoryDestination.SOL,
        brand="brand-created-after-deploy",
        text="Keep spontaneous everyday video behavior and grounded purchase occasions.",
        origin="CURATED", evidence_ids=("human-approved:new-brand",),
    )
    selection = select_memory(
        (rule,), brand=rule.brand, destination=MemoryDestination.SOL,
        director_mode="cross_scene_v2", mode="shadow",
    )
    assert selection.rule_ids == ("newbrand.ugc",)
    assert not selection.applied


def test_repeat_collection_is_idempotent_and_status_updates_append(fake):
    db, output = fake
    task, variant = _task(db, brand="tokyotiger", index=5)
    first = collect_once(db, output)
    assert first["new_events"] == 1
    assert collect_once(db, output)["new_events"] == 0
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE generation_variant SET final_approval_status='REJECTED' WHERE id=?",
            (variant,),
        )
    updated = collect_once(db, output)
    assert updated["new_events"] == 1
    event_file = sorted(output.glob("events-*.jsonl"))[-1]
    events = [
        json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 2
    assert events[0]["task_id"] == task
    assert events[0]["event_id"] != events[1]["event_id"]
    assert events[-1]["final_approval_status"] == "REJECTED"
    assert not events[0]["automatic_rule_promotion"]


def test_no_product_text_or_reviews_are_duplicated(fake):
    db, output = fake
    note = "PRIVATE ARTWORK WORDS NEVER COPIED TO SIDECAR"
    _task(db, brand="pagewings", index=9, note=note)
    collect_once(db, output)
    for f in output.iterdir():
        if f.suffix in (".jsonl", ".json"):
            raw = f.read_text(encoding="utf-8")
            assert note not in raw
            assert "PRIVATE_PRODUCT_HASH" not in raw
            assert "PRIVATE_STORY_HASH" not in raw
            assert "creativeBrief" not in raw
    assert read_snapshots(db)[0]["review_notes_present"] is True
    assert len(read_snapshots(db)[0]["review_notes_sha256"]) == 64


def test_non_ootd_and_custom_tasks_not_mistaken_for_training(fake):
    db, output = fake
    _task(db, brand="pagewings", index=2, video_type="unboxing")
    _task(db, brand="nerdywave", index=3, mode="custom")
    assert collect_once(db, output)["variants_read"] == 0


def test_missing_brand_is_quarantined_not_guessed(fake):
    db, output = fake
    _task(db, brand="BAD BRAND / UNKNOWN", index=2)
    report = collect_once(db, output)
    assert report["brands"]["unknown-unassigned"]["variants_seen"] == 1
    assert report["brands"]["pagewings"]["variants_seen"] == 0


def test_dry_run_does_not_write_anything(fake):
    db, output = fake
    _task(db, brand="guidingcross", index=4)
    report = collect_once(db, output, dry_run=True)
    assert report["variants_read"] == 1
    assert not output.exists()
    assert report["new_events"] == 0


def test_collector_lock_prevents_overlap(fake):
    db, output = fake
    _task(db, brand="pagewings", index=2)
    output.mkdir()
    (output / "collector.lock").write_text("another run")
    report = collect_once(db, output)
    assert report["already_running"] is True
    assert not list(output.glob("events-*.jsonl"))


def test_malformed_checkpoint_fails_closed(fake):
    db, output = fake
    _task(db, brand="pagewings", index=3)
    output.mkdir()
    (output / "state.json").write_text("CORRUPT")
    with pytest.raises(ValueError):
        collect_once(db, output)
    assert not list(output.glob("events-*.jsonl"))


def test_read_only_connection_does_not_require_video_bytes(fake):
    db, output = fake
    _task(db, brand="ironpandafit", index=2)
    before = db.read_bytes()
    result = collect_once(db, output)
    assert result["new_events"] == 1
    assert before == db.read_bytes()
    assert result["db_writes"] == 0


def test_bounds_and_missing_db(fake):
    db, output = fake
    with pytest.raises(ValueError):
        read_snapshots(db, limit=3001)
    with pytest.raises(FileNotFoundError):
        read_snapshots(output / "missing.db")


def test_history_summary_preserves_zero_brand_participation(fake):
    db, output = fake
    _task(db, brand="tokyotiger", index=1)
    result = collect_once(db, output)
    assert result["brands"]["tokyotiger"]["variants_seen"] == 1
    assert result["brands"]["geeksoutfit"]["variants_seen"] == 0
    s = json.loads((output / "latest-summary.json").read_text(encoding="utf-8"))
    assert s["variants_read"] == 1
