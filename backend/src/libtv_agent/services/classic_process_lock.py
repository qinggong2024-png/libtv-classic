"""Host-local Classic locks. Never unlink a live lock file.

All participating API processes must share the same private route directory.
A lock serializes execution, not provider billing; an interrupted remote
request still needs receipt reconciliation before any replay.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager, contextmanager


@contextmanager
def try_lock(settings, namespace: str, key: str = ""):
    route = settings.classic_api_route_file
    if route is None:
        yield True
        return
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    path = route.parent / f"api-{namespace}-{digest}.lock"
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
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@asynccontextmanager
async def job_lock(settings, namespace: str, key: str):
    while True:
        with try_lock(settings, namespace, key) as acquired:
            if acquired:
                yield
                return
        await asyncio.sleep(0.2)
