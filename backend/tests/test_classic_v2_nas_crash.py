"""Isolated separate OS process crash after immutable CUT handoff package rename."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from libtv_agent.services import cutflow_multi_worker as worker
from test_cutflow_handoff import handoff_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
CHILD = ROOT / "scripts" / "classic_v2_nas_crash_child.py"


@pytest.mark.asyncio
async def test_interrupted_final_nas_publish_repairs_only_the_original_ready(
    handoff_env, tmp_path,  # noqa: F811
):
    _database, settings, store, ids = handoff_env
    source_bytes = b"synthetic published media with fixed sha256"
    source = tmp_path / "ready-crash-video.mp4"
    source.write_bytes(source_bytes)
    object_key = await store.put_file(
        source, "multi/ready-crash-video.mp4", "video/mp4",
    )
    task_id = uuid.uuid4()
    created = datetime.now(UTC)
    sources = [{
        "clipId": "clip-1",
        "order": 1,
        "relativePath": "sources/01.mp4",
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "sizeBytes": len(source_bytes),
        "durationSeconds": 3.0,
        "userId": str(ids["user"]),
        "taskId": "unchanged-original-task",
        "variantId": "unchanged-original-variant",
        "artifactId": "unchanged-original-artifact",
        "attemptId": "unchanged-original-attempt",
        "objectKey": object_key,
    }]
    task = SimpleNamespace(
        id=task_id,
        user_id=ids["user"],
        created_at=created,
        sources_json=sources,
    )
    fixture = tmp_path / "nas-crash.json"
    fixture.write_text(json.dumps({
        "handoffRoot": str(settings.cutflow_handoff_root),
        "artifactRoot": str(settings.artifact_root),
        "objectRoot": str(tmp_path / "objects"),
        "taskId": str(task_id),
        "userId": str(ids["user"]),
        "createdAt": created.isoformat(),
        "sources": sources,
    }), encoding="utf-8")
    child_env = {
        **os.environ,
        "ALLWE_V2_FAULT_INJECTION": "LOCAL_ONLY",
        "PYTHONPATH": str(ROOT / "backend" / "src"),
    }
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(CHILD), "--fixture", str(fixture)],
        env=child_env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert child.returncode == 87, (
        f"expected deliberate post-rename crash; got {child.returncode}: "
        f"{child.stderr[:400]}"
    )
    final = settings.cutflow_handoff_root / "inbox" / f"multi-{task_id}"
    assert final.is_dir()
    original_manifest = (final / "manifest.json").read_bytes()
    assert (final / "sources" / "01.mp4").read_bytes() == source_bytes
    assert not (final / "READY").exists()
    descriptor, digest = await worker.publish_multi_package(task, settings, store)
    assert digest == hashlib.sha256(original_manifest).hexdigest()
    assert json.loads((final / "READY").read_text(encoding="utf-8")) == {
        "sha256": digest,
    }
    assert descriptor["taskId"] == str(task_id)
    assert (final / "manifest.json").read_bytes() == original_manifest
    assert (final / "sources" / "01.mp4").read_bytes() == source_bytes
    assert [p.name for p in (settings.cutflow_handoff_root / "inbox").iterdir()
            if p.is_dir() and p.name.startswith("multi-")] == [
        f"multi-{task_id}"
    ], "recovery must not generate a duplicate CUT package identity"
