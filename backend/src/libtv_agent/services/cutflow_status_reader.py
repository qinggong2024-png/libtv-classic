"""Read-only, bounded-size NAS snapshot reader used in a disposable child.

Only this small process may be terminated on timeout, never the worker or a
remote CUT task. No DB access, environment secrets, task submit or file writes.
"""
from __future__ import annotations

import errno
import os
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 1_048_576
MISSING = 3
UNSAFE = 4
UNAVAILABLE = 5
OVERSIZE = 6
AUTH_FAILED = 7
ACCESS_DENIED = 8
UNREACHABLE = 9

ERROR_CODES = {
    AUTH_FAILED: "NAS_AUTH_FAILED",
    ACCESS_DENIED: "NAS_ACCESS_DENIED",
    UNREACHABLE: "NAS_UNREACHABLE",
}


def error_exit(exc: OSError) -> int:
    winerror = getattr(exc, "winerror", None)
    if winerror in {86, 1326, 1330, 1909, 1219}:
        return AUTH_FAILED
    if winerror in {5, 65} or exc.errno in {errno.EACCES, errno.EPERM}:
        return ACCESS_DENIED
    if winerror in {53, 64, 67, 121, 1231, 1232} or exc.errno in {
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT,
    }:
        return UNREACHABLE
    return UNAVAILABLE


def probe(root: Path) -> None:
    """Test the worker's own SMB context, never a business task or CUT output."""
    # Missing required directories are unhealthy, not silently created.
    for relative in ("inbox", "outbox", "receipts/classic"):
        directory = root / relative
        with os.scandir(directory) as entries:
            next(entries, None)
        if relative == "outbox":
            continue  # CUT owns outbox; ALLWE only needs read access there.
        # Non-contract temporary filename, never a READY, manifest or receipt.
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".allwe-nas-health-", suffix=".tmp", delete=False,
            ) as handle:
                path = Path(handle.name)
                handle.write(b"allwe-nas-health")
                handle.flush()
                os.fsync(handle.fileno())
            if path.read_bytes() != b"allwe-nas-health":
                raise OSError("probe verification failed")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


def read_snapshot(root: Path, relative: str) -> bytes:
    if (
        not relative or "\\" in relative or ":" in relative or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("unsafe relative path")
    canonical = root.resolve()
    target = (canonical / relative).resolve()
    if not target.is_relative_to(canonical) or target == canonical:
        raise ValueError("unsafe snapshot path")
    if target.exists() and not target.is_file():
        raise OSError("not a regular file")
    with target.open("rb") as handle:
        return handle.read(MAX_BYTES + 1)


def main() -> int:
    if len(sys.argv) != 3:
        return UNSAFE
    if sys.argv[2] == "--probe":
        try:
            probe(Path(sys.argv[1]))
            return 0
        except OSError as exc:
            return error_exit(exc)
    try:
        raw = read_snapshot(Path(sys.argv[1]), sys.argv[2])
    except FileNotFoundError as exc:
        classified = error_exit(exc)
        return MISSING if classified == UNAVAILABLE else classified
    except ValueError:
        return UNSAFE
    except OSError as exc:
        return error_exit(exc)
    if len(raw) > MAX_BYTES:
        return OVERSIZE
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
