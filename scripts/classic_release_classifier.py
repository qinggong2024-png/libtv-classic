"""Conservative release-change classifier for ALLWE Classic 5176.

Classifies *committed Git paths*, never task status alone. This is a safety
boundary, not a content audit: frontend-only still requires reviewed API
compatibility, an active-backend fingerprint, a pinned frontend index and tests.
A missing/unknown path category fails closed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SHA40 = re.compile(r"^[0-9a-f]{40}$")
FRONTEND_EXT = frozenset({".tsx", ".ts", ".css", ".scss", ".svg", ".json"})
STATIC_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2"})
RUNNER_NAMES = ("runner", "worker", "cutflow", "dispatch", "submission", "receipt")
PROMPT_NAMES = ("prompt", "system_prompt", "negative_prompt")


def classify_path(path: str) -> str:
    """The allowlist avoids mistaking scripts and API contracts for UI changes."""
    if not path or path.startswith("/") or ".." in path.split("/") or "\\" in path:
        return "unknown"
    lower = path.casefold()
    name = Path(lower).name
    if lower.startswith("backend/alembic/") or lower.startswith("backend/src/libtv_agent/domain/models"):
        return "schema"
    if name in {"pyproject.toml", "uv.lock", "alembic.ini", "package-lock.json", "pnpm-lock.yaml"}:
        return "dependencies"
    if any(word in name for word in PROMPT_NAMES) and (
        lower.startswith(("backend/", "scripts/", "frontend/src/"))
    ):
        return "prompt"
    if lower.startswith("frontend/src/"):
        if lower.startswith(("frontend/src/api", "frontend/src/services/",
                             "frontend/src/domain/", "frontend/src/config")):
            return "frontend-contract"
        return "frontend" if Path(lower).suffix in FRONTEND_EXT else "unknown"
    if lower.startswith("frontend/public/"):
        return "frontend" if Path(lower).suffix in STATIC_EXT else "unknown"
    if lower == "frontend/index.html":
        return "frontend"
    if lower.startswith("frontend/"):
        return "frontend-test" if name.endswith((".test.ts", ".test.tsx")) else "release-infra"
    if lower.startswith("backend/tests/"):
        return "test"
    if lower.startswith("backend/src/libtv_agent/"):
        if any(x in name for x in RUNNER_NAMES) or lower.startswith("backend/src/libtv_agent/services/"):
            return "worker"  # Services are shared by API and paid workers: conservative.
        return "api"
    if lower.startswith("backend/"):
        return "dependencies"  # Config/migrations/schema require compatibility review.
    if lower.startswith("scripts/"):
        if any(x in name for x in RUNNER_NAMES):
            return "worker"
        return "release-infra"
    if lower.startswith("docs/") or lower in {"readme.md", "classic_readme.md", "agents.md", "classic_workspace.md"}:
        return "docs"
    if lower.startswith(".github/"):
        return "release-infra"
    return "unknown"


def classify_changes(paths: list[str]) -> dict:
    by_type: dict[str, list[str]] = {}
    for path in sorted(set(paths)):
        by_type.setdefault(classify_path(path), []).append(path)
    kinds = set(by_type)
    actual = kinds - {"test", "frontend-test", "docs"}
    if not actual:
        mode = "no-runtime-change"
    elif actual <= {"frontend"}:
        mode = "frontend-hot"
    elif "schema" in actual or "dependencies" in actual:
        mode = "maintenance-review"
    elif "prompt" in actual:
        mode = "prompt-approval-required"
    elif "unknown" in actual or "release-infra" in actual:
        mode = "manual-review"
    elif "worker" in actual:
        mode = "worker-handoff-required"
    elif actual <= {"api", "frontend-contract", "frontend"}:
        mode = "api-canary-review"
    else:
        mode = "manual-review"
    return {
        "mode": mode,
        "fileCount": len(paths),
        "byType": by_type,
        "frontendHotEligible": mode == "frontend-hot",
        "requiresQuiescence": bool(actual & {"worker", "schema", "dependencies", "prompt"}),
        "requiresManualReview": mode not in {"no-runtime-change", "frontend-hot"},
        "notes": ("Frontend-hot eligibility is path-based; a human must still check API "
                  "compatibility and approve the exact diff. No worker or paid task may "
                  "be interrupted by the classifier."),
    }


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return result.stdout


def committed_changes(root: Path, base: str, target: str = "HEAD") -> dict:
    """Require a real ancestor range; no guessed or uncommitted baseline."""
    try:
        base_sha = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        target_sha = _git(root, "rev-parse", "--verify", f"{target}^{{commit}}").decode().strip()
    except (subprocess.CalledProcessError, UnicodeError) as error:
        raise ValueError("Invalid release baseline or candidate commit") from error
    if not SHA40.fullmatch(base_sha) or not SHA40.fullmatch(target_sha):
        raise ValueError("Resolved Git commit is not a full SHA")
    if base_sha == target_sha:
        raise ValueError("Change baseline and candidate must differ")
    is_ancestor = subprocess.run(["git", "-C", str(root), "merge-base",
                                  "--is-ancestor", base_sha, target_sha],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=20, check=False)
    if is_ancestor.returncode != 0:
        raise ValueError("Baseline is not an ancestor of the release candidate")
    raw = _git(root, "diff", "--name-only", "--no-renames", "-z", base_sha, target_sha)
    paths = [s.decode("utf-8") for s in raw.split(b"\0") if s]
    result = classify_changes(paths)
    result.update({"base": base_sha, "target": target_sha, "paths": paths})
    return result
