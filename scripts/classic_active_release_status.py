"""Read-only source-of-truth report for the Classic 5176 blue/green deployment.

This tool never edits Git, SQLite, HTTP routing, processes, intake gates,
media objects or billing. It identifies *running* API code separately from
the checkout at the historical main delivery path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "var" / "classic-lan" / "runtime"
GALLERY_FILES = (
    "backend/src/libtv_agent/api/admin.py",
    "backend/src/libtv_agent/services/admin_video_tasks.py",
    "frontend/src/features/admin/AdminCenterView.tsx",
    "frontend/src/features/admin/AdminVideoTaskReview.tsx",
)
URL = "http://127.0.0.1:5176"


def get_json(url: str) -> dict | None:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url, timeout=3) as response:
            if response.status != 200:
                return None
            data = json.load(response)
            return data if isinstance(data, dict) else None
    except (OSError, ValueError, HTTPError, URLError):
        return None


def read_json_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def git_head(path: Path) -> str | None:
    try:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        p = subprocess.run(
            ["git", "-c", "core.commitGraph=false", "-C", str(path), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=3, env=env, check=True,
        )
        return p.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def workspace_source_revision(project_root: Path) -> str | None:
    """Reproduce backend source fingerprint without loading app configuration."""
    source_files = [
        *((project_root / "backend" / "src").rglob("*.py")),
        *((project_root / "backend" / "alembic").rglob("*.py")),
        project_root / "backend" / "alembic.ini",
        project_root / "pyproject.toml",
        project_root / "uv.lock",
    ]
    if not all(path.is_file() for path in source_files):
        return None
    entries = []
    try:
        for path in sorted(
            source_files,
            key=lambda value: value.relative_to(project_root).as_posix().casefold(),
        ):
            relative = path.relative_to(project_root).as_posix()
            digest = sha256(path)
            if digest is None:
                return None
            entries.append(f"{relative}\0{digest}")
    except OSError:
        return None
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def unsynced_files(active_root: Path, main_root: Path) -> list[str]:
    return [
        filename for filename in GALLERY_FILES
        if sha256(active_root / filename) != sha256(main_root / filename)
    ]


def report(root: Path = ROOT) -> dict:
    route = get_json(URL + "/__classic_api_route/status")
    gate = get_json(URL + "/api/v1/system/classic-new-video-admission")
    if not route or route.get("activeApiPort") not in (8002, 8003):
        return {"ok": False, "error": "Classic 5176 hot route unavailable"}
    active_port = route["activeApiPort"]
    active_status = get_json(f"http://127.0.0.1:{active_port}/api/v1/system/status")
    standby_port = 8003 if active_port == 8002 else 8002
    standby_status = get_json(f"http://127.0.0.1:{standby_port}/api/v1/system/status")
    health = get_json(URL + "/api/v1/health/ready")
    manifest = read_json_file(root / "var" / "classic-lan" / "runtime" / "green-api.json")
    blue_pin = read_json_file(root / "var" / "classic-lan" / "runtime" / "blue-api-pin.json")
    blue_record = None
    if blue_pin:
        stack = read_json_file(root / "var" / "classic-lan" / "runtime" / "stack.json") or {}
        blue_record = next((record for record in stack.get("processes", [])
                            if record.get("name") == "api"), None)
    if active_port == 8002:
        active_root = (
            Path(blue_record["releaseRoot"]) if blue_record
            and isinstance(blue_record.get("releaseRoot"), str) else (root if not blue_pin else None)
        )
    else:
        active_root = (
            Path(manifest["releaseRoot"]) if manifest and isinstance(manifest.get("releaseRoot"), str)
            else None
        )
    manifest_matches = (
        manifest is not None
        and active_root is not None
        and manifest.get("sourceRevision") == (active_status or {}).get("sourceRevision")
    ) if active_port == 8003 else None
    blue_manifest_matches = (
        bool(blue_record and active_root
             and blue_pin.get("sourceRevision") == blue_record.get("sourceRevision")
             == (active_status or {}).get("sourceRevision")
             and blue_pin.get("releaseRoot") == blue_record.get("releaseRoot"))
        if active_port == 8002 and blue_pin else None
    )
    pending_admin_sync = unsynced_files(active_root, root) if active_root and active_root.is_dir() else []
    active_disk_revision = workspace_source_revision(active_root) if active_root and active_root.is_dir() else None
    main_disk_revision = workspace_source_revision(root)
    running_revision = (active_status or {}).get("sourceRevision")
    source = {
        "root": str(active_root) if active_root else None,
        "gitHead": git_head(active_root) if active_root else None,
        "sourceRevision": running_revision,
        "diskMatchesRunning": active_disk_revision == running_revision if active_disk_revision else None,
    }
    main = {"root": str(root), "gitHead": git_head(root),
            "sourceRevisionOnDisk": main_disk_revision,
            "matchesRunning": main_disk_revision == running_revision if main_disk_revision else None}
    issues = []
    if active_status is None:
        issues.append("Active API status endpoint is unavailable")
    if health is None:
        issues.append("Classic site readiness check is unavailable")
    if gate is None:
        issues.append("Admission gate status unavailable")
    if active_port == 8003 and not manifest_matches:
        issues.append("Green API source fingerprint does not match recorded release manifest")
    if blue_manifest_matches is False:
        issues.append("Blue API source fingerprint does not match its pinned process record")
    if not active_root or not active_root.is_dir():
        issues.append("Active source directory could not be located")
    if active_disk_revision and active_disk_revision != running_revision:
        issues.append("Active API source files differ from their running startup fingerprint")
    if main_disk_revision and main_disk_revision != running_revision:
        issues.append("Main source contains changes that are not loaded in the currently active API")
    if pending_admin_sync:
        issues.append("Latest main checkout is missing/different from active administrator-gallery source")
    entry = root / "frontend" / "dist" / "index.html"
    return {
        "ok": (active_status is not None and health is not None and manifest_matches is not False
               and blue_manifest_matches is not False
               and (active_disk_revision is None or active_disk_revision == running_revision)),
        "userUrl": "http://192.168.30.126:5176/",
        "activeApiPort": active_port,
        "standbyApiPort": standby_port,
        "admissionPaused": gate.get("newVideoAdmissionsPaused") if gate else None,
        "activeApi": source,
        "mainCheckout": main,
        "standbyApiSourceRevision": (standby_status or {}).get("sourceRevision"),
        "activeConfigRevision": (active_status or {}).get("configRevision"),
        "activeSchemaRevision": (active_status or {}).get("schemaRevision"),
        "greenManifestMatchesRunningApi": manifest_matches,
        "blueManifestMatchesRunningApi": blue_manifest_matches,
        "frontendIndexSha256": sha256(entry),
        "galleryFilesDifferentFromMain": pending_admin_sync,
        "notes": issues,
    }



def verify_release(state: dict, *, expected_api_revision: str | None,
                   expected_frontend_sha256: str | None,
                   expected_main_head: str | None) -> list[str]:
    """Fail closed if a planned production release cannot be independently proven.

    Expected values come from a frozen approved release candidate and the built
    bundle, not from copying the current live response after deployment.
    This check never changes routing, jobs, storage or intake.
    """
    issues = []
    if not state.get("ok"):
        issues.append("The active Classic API or readiness checks are not healthy")
    if state.get("userUrl") != "http://192.168.30.126:5176/":
        issues.append("Classic user-facing URL does not match the fixed 5176 entry")
    if state.get("activeApiPort") not in (8002, 8003):
        issues.append("Active API port is outside the Classic 8002/8003 pool")
    if state.get("standbyApiPort") not in (8002, 8003) or state.get("standbyApiPort") == state.get("activeApiPort"):
        issues.append("Standby API port is invalid")
    if state.get("admissionPaused") is not False:
        issues.append("New video task admission has not been reopened")
    active = state.get("activeApi") or {}
    main = state.get("mainCheckout") or {}
    if active.get("diskMatchesRunning") is not True:
        issues.append("Active API on-disk code does not match its running source fingerprint")
    if main.get("matchesRunning") is not True:
        issues.append("Production main source is not identical to the running API code")
    if state.get("galleryFilesDifferentFromMain"):
        issues.append("Main checkout and active release have differing administrator sources")
    if expected_api_revision is None or len(expected_api_revision) != 64 or any(
        character not in "0123456789abcdef" for character in expected_api_revision
    ):
        issues.append("Supply the approved 64-digit API source fingerprint")
    elif active.get("sourceRevision") != expected_api_revision:
        issues.append("Running API does not match the approved release source fingerprint")
    if expected_frontend_sha256 is None or len(expected_frontend_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_frontend_sha256
    ):
        issues.append("Supply the approved 64-digit frontend index SHA256")
    elif state.get("frontendIndexSha256") != expected_frontend_sha256:
        issues.append("Frontend index does not match the approved release bundle")
    if expected_main_head is None or len(expected_main_head) != 40 or any(
        character not in "0123456789abcdef" for character in expected_main_head
    ):
        issues.append("Supply the approved 40-digit main Git commit")
    elif main.get("gitHead") != expected_main_head:
        issues.append("Main checkout does not match the approved release commit")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the read-only report as JSON")
    parser.add_argument("--verify-release", action="store_true",
                        help="Fail closed unless deployed API, main and frontend match an approved release")
    parser.add_argument("--expected-api-revision", help="Approved full 64-digit API sourceRevision")
    parser.add_argument("--expected-frontend-sha256", help="Approved 64-digit frontend index SHA256")
    parser.add_argument("--expected-main-head", help="Approved full 40-digit main Git commit")
    args = parser.parse_args()
    state = report()
    if args.verify_release:
        reasons = verify_release(
            state, expected_api_revision=args.expected_api_revision,
            expected_frontend_sha256=args.expected_frontend_sha256,
            expected_main_head=args.expected_main_head,
        )
        state["releaseAcceptance"] = {"passed": not reasons, "blockers": reasons}
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(f"Classic site: {state.get('userUrl', URL)}")
        print(f"Active API: {state.get('activeApiPort')} (standby {state.get('standbyApiPort')})")
        print(f"Active source: {state.get('activeApi', {}).get('root')}")
        print(f"Active commit: {state.get('activeApi', {}).get('gitHead')}")
        print(f"Main checkout: {state.get('mainCheckout', {}).get('gitHead')}")
        print(f"New-task gate paused: {state.get('admissionPaused')}")
        print(f"Frontend index SHA256: {state.get('frontendIndexSha256')}")
        for line in state.get("notes", []):
            print("NOTE:", line)
        if args.verify_release:
            print("Release acceptance:", "PASS" if state["releaseAcceptance"]["passed"] else "BLOCKED")
            for blocker in state["releaseAcceptance"]["blockers"]:
                print("BLOCKER:", blocker)
    return 0 if state.get("ok") and (not args.verify_release or state["releaseAcceptance"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
