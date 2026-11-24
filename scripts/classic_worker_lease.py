"""OS-owned lifetime lock for classic Runner / CUT single-writer processes.

Held for the ENTIRE event loop, not a per-task transient lease. The OS releases
it if a process dies. Never forcibly steal/recreate the file while a worker lives.
Both blue and green versions must point to the SAME private runtime directory.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ClassicWorkerAlreadyRunning(RuntimeError):
    pass


@contextmanager
def exclusive_classic_worker(kind: str, runtime: Path) -> Iterator[None]:
    if kind not in {"runner", "cutflow", "api-green", "api-blue"}:
        raise ValueError("unrecognized classic worker")
    if not runtime.is_absolute() or not runtime.is_dir():
        raise ValueError("classic worker runtime directory must already exist")
    path = runtime / f"singleton-{kind}.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ClassicWorkerAlreadyRunning(
                    f"classic {kind} already has an exclusive worker"
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ClassicWorkerAlreadyRunning(
                    f"classic {kind} already has an exclusive worker"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
