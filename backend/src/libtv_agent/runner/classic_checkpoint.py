"""Cooperative Classic worker checkpoint, scoped to a single process lifetime.

A request never kills a process. The lifetime singleton remains held until all
local work has returned and the database has closed. Cloud work is not drained
here; existing receipts/commands are left intact.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import psutil


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class CheckpointStop(threading.Event):
    def __init__(self, runtime: Path, kind: str, source_revision: str):
        super().__init__()
        if kind not in ("runner", "cutflow"):
            raise ValueError("invalid worker")
        if len(source_revision) != 64 or any(c not in "0123456789abcdef" for c in source_revision):
            raise ValueError("invalid source revision")
        self.runtime = runtime
        self.kind = kind
        self.identity = {
            "version": 1,
            "kind": kind,
            "pid": os.getpid(),
            "created": psutil.Process().create_time(),
            "token": uuid.uuid4().hex,
            "sourceRevision": source_revision,
        }
        self.request_path = runtime / f"checkpoint-{kind}-request.json"
        self.status_path = runtime / f"checkpoint-{kind}-status.json"
        self.publish("RUNNING")

    def publish(self, state: str) -> None:
        atomic_json(self.status_path, {**self.identity, "state": state})

    def is_set(self) -> bool:
        if super().is_set():
            return True
        try:
            request = json.loads(self.request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if request == self.identity:
            self.set()
            return True
        return False

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            delay = 0.2 if deadline is None else min(0.2, deadline - time.monotonic())
            if delay <= 0:
                return False
            super().wait(delay)
        return True
