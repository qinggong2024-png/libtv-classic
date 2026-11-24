"""Read-only immutable backend source selection for the two Classic workers."""
from __future__ import annotations

import json
from pathlib import Path


def worker_source(kind: str, runtime: Path, root: Path) -> tuple[Path, str]:
    from classic_active_release_status import workspace_source_revision

    if kind not in ("runner", "cutflow"):
        raise ValueError("invalid worker kind")
    path = runtime / f"worker-{kind}-pin.json"
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        revision = workspace_source_revision(root)
        if revision is None:
            raise RuntimeError("worker source fingerprint unavailable") from None
        return root, revision
    if not isinstance(pin, dict) or set(pin) != {"version", "releaseRoot", "sourceRevision"}:
        raise RuntimeError("invalid worker source pin")
    if type(pin["version"]) is not int or pin["version"] != 1:
        raise RuntimeError("invalid worker pin version")
    release = Path(pin["releaseRoot"]).resolve()
    if release == root.resolve() or release.parent != root.resolve().parent:
        raise RuntimeError("worker source must be an isolated sibling release")
    if (release / ".env").exists() or list((release / "frontend").glob(".env*")):
        raise RuntimeError("worker release must not include environment overrides")
    revision = workspace_source_revision(release)
    if revision is None or pin["sourceRevision"] != revision:
        raise RuntimeError("worker source fingerprint mismatch")
    return release, revision
