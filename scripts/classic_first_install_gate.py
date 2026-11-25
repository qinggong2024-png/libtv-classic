"""Read-only gate for the FIRST Classic 5176 hot-route/intake installation.

Does not pause admission, touch DB data, modify Git, stop processes or start
paid providers. The legacy 5176 server has no atomic intake pause, therefore
even PASS means only "ready to schedule a controlled maintenance ingress
freeze", NEVER permission to silently restart a busy production instance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from classic_v2_canary_preflight import check

BASELINE_UNKNOWN = 15


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _http_status(url: str) -> tuple[int, str]:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=4) as response:
            return response.status, response.headers.get("Content-Type", "")
    except HTTPError as error:
        return error.code, error.headers.get("Content-Type", "")
    except (OSError, URLError):
        return 0, ""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inspect_backups(runtime: Path, now: datetime) -> dict:
    root = runtime / "release-backups"
    candidates = sorted(root.glob("classic-*/manifest.json"), reverse=True)
    failures: list[str] = []
    for path in candidates:
        folder = path.parent
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            db = folder / "classic.db"
            frontend = folder / "frontend-dist-before-v2.zip"
            frontend_meta = json.loads(
                (folder / "frontend-manifest.json").read_text(encoding="utf-8-sig")
            )
            when = datetime.fromisoformat(manifest["createdAtUtc"])
            if when.tzinfo is None or not (
                timedelta(seconds=-60) <= now - when <= timedelta(hours=2)
            ):
                raise ValueError("backup is not fresh")
            if (
                manifest.get("integrity") != "ok"
                or manifest.get("restoredCloneReadable") is not True
            ):
                raise ValueError("database restore proof missing")
            if _digest(db) != manifest.get("sha256"):
                raise ValueError("database backup hash mismatch")
            if _digest(frontend) != frontend_meta.get("sha256"):
                raise ValueError("frontend rollback archive hash mismatch")
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3) as snapshot:
                snapshot.execute("PRAGMA query_only=ON")
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("database snapshot integrity failure")
            return {
                "verified": True,
                "ageMinutes": round((now - when).total_seconds() / 60, 1),
                "databaseBytes": db.stat().st_size,
                "frontendBytes": frontend.stat().st_size,
            }
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            failures.append(type(error).__name__)
    return {"verified": False, "rejectedBackupCandidates": len(failures)}


def release_readiness(
    main_root: Path,
    candidate_root: Path,
    *,
    now: datetime | None = None,
) -> dict:
    main_root = main_root.resolve(strict=True)
    candidate_root = candidate_root.resolve(strict=True)
    if main_root == candidate_root:
        raise ValueError("Release rehearsal must be separate from the live checkout")
    now = now or datetime.now(UTC)
    issues: list[str] = []
    main_head = _git(main_root, "rev-parse", "--short", "HEAD")
    release_head = _git(candidate_root, "rev-parse", "--short", "HEAD")
    if _git(main_root, "status", "--porcelain"):
        issues.append("PRODUCTION_MAIN_DIRTY")
    if _git(candidate_root, "status", "--porcelain"):
        issues.append("RELEASE_CANDIDATE_DIRTY")
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(candidate_root),
            "merge-base",
            "--is-ancestor",
            _git(main_root, "rev-parse", "HEAD"),
            "HEAD",
        ],
        capture_output=True,
        timeout=10,
    )
    if ancestor.returncode != 0:
        issues.append("RELEASE_NOT_BASED_ON_CURRENT_MAIN")

    instance = main_root / "var" / "classic-lan"
    try:
        recorded = json.loads((instance / "runtime" / "stack.json").read_text("utf-8"))
        if Path(recorded["root"]).resolve() != main_root:
            issues.append("PRODUCTION_PROCESS_ROOT_MISMATCH")
        if not {"api", "web", "runner", "cutflow"}.issubset(
            {item["name"] for item in recorded.get("processes", [])}
        ):
            issues.append("PRODUCTION_PROCESS_LIST_MISMATCH")
    except (OSError, ValueError, KeyError, TypeError):
        issues.append("PRODUCTION_STACK_RECORD_UNREADABLE")

    tasks = check(instance / "classic.db", baseline_uncertain=BASELINE_UNKNOWN)
    if not tasks["quiescentForVersionSwitch"]:
        issues.extend(tasks["blockers"])

    if _http_status("http://127.0.0.1:5176/api/v1/health/ready")[0] != 200:
        issues.append("PRODUCTION_HEALTH_NOT_READY")
    gate_http, _ = _http_status("http://127.0.0.1:8002/api/v1/system/classic-new-video-admission")
    if gate_http != 404:
        issues.append("FIRST_INSTALL_GATE_STATE_CHANGED")
    route_http, route_type = _http_status("http://127.0.0.1:5176/__classic_api_route/status")
    if route_http != 200 or "text/html" not in route_type.lower():
        issues.append("FIRST_INSTALL_ROUTE_STATE_CHANGED")

    backup = inspect_backups(instance / "runtime", now)
    if not backup["verified"]:
        issues.append("BACKUP_NOT_CURRENT_AND_VERIFIED")

    return {
        "checkedAtUtc": now.isoformat(),
        "mode": "read-only first-install preparation; does not freeze ingress",
        "liveRevision": main_head,
        "candidateRevision": release_head,
        "counts": tasks["counts"],
        "backup": backup,
        "blockers": sorted(set(issues)),
        "readyForControlledMaintenance": not issues,
        "ingressFreezeInstalled": False,
        "manualStepsRequired": [
            "Agree a brief maintenance window before changing 5176",
            "Freeze ALL external new submissions; legacy server cannot atomically pause",
            "Rerun full preflight after that ingress freeze",
            "Snapshot current database and hashed frontend build again",
            "Start replacement API/web under a supervised one-writer handoff",
            "Validate both route and admission JSON handshakes before reopening ingress",
        ],
        "productionChanged": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    report = release_readiness(args.live_root, args.candidate_root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["readyForControlledMaintenance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
