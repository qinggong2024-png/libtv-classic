"""Child-only injected crash at the final immutable CUT package publish boundary.

Runs ONLY with ALLWE_V2_FAULT_INJECTION=LOCAL_ONLY and all roots beneath the OS
temporary directory. There is no CUT HTTP POST, cloud submission or payment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from libtv_agent.adapters.object_store import LocalObjectStore
from libtv_agent.config import Settings
from libtv_agent.services import cutflow_multi_worker

EXIT_AFTER_RENAME = 87


async def simulate_crash(spec: dict) -> None:
    root = Path(spec["handoffRoot"])
    artifact = Path(spec["artifactRoot"])
    objects = Path(spec["objectRoot"])
    task_id = uuid.UUID(spec["taskId"])
    settings = Settings(
        _env_file=None, app_env="test",
        cutflow_handoff_enabled=True,
        cutflow_handoff_root=root, artifact_root=artifact,
    )
    task = SimpleNamespace(
        id=task_id,
        user_id=uuid.UUID(spec["userId"]),
        created_at=datetime.fromisoformat(spec["createdAt"]),
        sources_json=spec["sources"],
    )
    store = LocalObjectStore(objects)
    final = root / "inbox" / f"multi-{task_id}"
    native_replace = cutflow_multi_worker.os.replace

    def crash_after_publish(src, dst):
        if Path(dst) == final:
            native_replace(src, dst)
            # Inject the SMB/power-loss symptom (lost READY marker) only after
            # the immutable manifest and verified source were published.
            (final / "READY").unlink()
            os._exit(EXIT_AFTER_RENAME)
        return native_replace(src, dst)

    cutflow_multi_worker.os.replace = crash_after_publish
    await cutflow_multi_worker.publish_multi_package(task, settings, store)
    raise RuntimeError("expected process crash did not occur")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("ALLWE_V2_FAULT_INJECTION") != "LOCAL_ONLY":
        raise SystemExit("fault-injection guard missing")
    fixture = args.fixture.resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if temp not in fixture.parents or not fixture.is_file():
        raise SystemExit("only a temporary synthetic fixture is allowed")
    spec = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise SystemExit("invalid fixture")
    for key in ("handoffRoot", "artifactRoot", "objectRoot"):
        safe = Path(spec[key]).resolve()
        if temp not in safe.parents:
            raise SystemExit("all filesystem roots must be temporary")
    asyncio.run(simulate_crash(spec))
    return 1


if __name__ == "__main__":
    sys.exit(main())
