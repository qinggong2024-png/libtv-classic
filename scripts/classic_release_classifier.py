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


# Exact reviewed API-only compatibility pairs shared by routing and frontend continuity.
API_ONLY_PAIR = frozenset({
    "9b01a07df26943ac8f59970a89fd757db0acb7cbe0c021f175789b3b3261baf2",
    "800bb2238764b40f417c9ffe46bff06facc92ded2225915513121a011c20d3f4",
})


# Reviewed production-activity pair: exactly accounts.py and its read-only
# projection module; no production writers, workers, schema or config changes.
ACTIVITY_API_ONLY_PAIR = frozenset({
    "800bb2238764b40f417c9ffe46bff06facc92ded2225915513121a011c20d3f4",
    "c2c53ddf70c12ba45b9f21371b341fff2f0b5fa2418e30a1494d56e590c11844",
})


# Image materialization only: same provider observation/IDs/schema/Runner;
# persist URLs before downloads, then fence each result against old API writes.
IMAGE_API_ONLY_PAIR = frozenset({
    "c2c53ddf70c12ba45b9f21371b341fff2f0b5fa2418e30a1494d56e590c11844",
    "851992dc658ddcb52fb59465b9198d64a8c8707c176becd08ee41abc8168346f",
})


# Only API enqueue gates and the durable config reader differ. No provider,
# Worker, schema, or actual shared configuration mutation is part of this pair.
RUNNER_COMPAT_API_PAIR = frozenset({
    "851992dc658ddcb52fb59465b9198d64a8c8707c176becd08ee41abc8168346f",
    "9fa2b54595dbb067ebd9c3bc691454e1d9ed9ec072d7527bb7a557e66554f7aa",
})


# Reviewed upload transaction-only pair; no provider, Runner, schema or route contract changes.
UPLOAD_TX_API_PAIR = frozenset({
    "9fa2b54595dbb067ebd9c3bc691454e1d9ed9ec072d7527bb7a557e66554f7aa",
    "ad6d7b8ed034520060f8f9df71ae9dde35e1e018c037a7ed05215a198d4a8edb",
})


# Exact reliability API pair: short admission transactions and conditional
# receipt cleanup. No schema, startup, execution protocol or provider adapter
# change. The running worker and all standby/drain checks remain authoritative.
RELIABILITY_ADMISSION_API_PAIR = frozenset({
    "ad6d7b8ed034520060f8f9df71ae9dde35e1e018c037a7ed05215a198d4a8edb",
    "9daa74dc429b49c18f26bdda720ccccaec00641deb2c79d42f8bf46e858e31e6",
})


# Exact Sol error-classification change already loaded by the reviewed Runner.
# API startup, durable ownership, provider adapters, dependencies and schema unchanged.
SOL400_API_PAIR = frozenset({
    "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61",
    "d3d5116d28d051c72dfe749643cd793981bc757bf07e288a0e8146a0b9b1ffbc",
})


# Shared planner transport only, activated independently in the Runner first.
# API startup, paid adapters, durable ownership, schema and prompts are unchanged.
SOL_JPEG_API_PAIR = frozenset({
    "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61",
    "3ea8fb51840e8a1356e276afabe1841b9aa3fc6c423e247e8872a999d703e206",
})


# Only the shared Sol response transport changes; API contract and schema unchanged.
SOL_STREAM_API_PAIR = frozenset({
    "3ea8fb51840e8a1356e276afabe1841b9aa3fc6c423e247e8872a999d703e206",
    "1fe56175794093db64d0c734e29aae2eed576f26315b489655b2c23e7768ce95",
})


# Exact admin gallery read-only projection: include tasks before artifacts.
# No API startup, schema, Worker, provider, billing, or request contract changes.
ADMIN_PREVIDEO_API_PAIR = frozenset({
    "1fe56175794093db64d0c734e29aae2eed576f26315b489655b2c23e7768ce95",
    "ff20528ca7f82e6695962460bea9f5fe96bd35e1814ee6dfe4937e85cd1ab19e",
})


# Exact additive administrator-only saved-media/trace projection; no paid execution,
# schema, startup, worker, or provider change.
ADMIN_DEEP_TRACE_API_PAIR = frozenset({
    "ff20528ca7f82e6695962460bea9f5fe96bd35e1814ee6dfe4937e85cd1ab19e",
    "c97c2e7b73546abd3adc57c9d72548522102ce8af712257e1c71a3a3fec64393",
})


def reviewed_pair(first, second):
    return frozenset((first, second)) in (
        API_ONLY_PAIR, ACTIVITY_API_ONLY_PAIR, IMAGE_API_ONLY_PAIR, RUNNER_COMPAT_API_PAIR, UPLOAD_TX_API_PAIR, RELIABILITY_ADMISSION_API_PAIR, SOL400_API_PAIR, SOL_JPEG_API_PAIR, SOL_STREAM_API_PAIR, ADMIN_PREVIDEO_API_PAIR, ADMIN_DEEP_TRACE_API_PAIR,
    )



def reviewed_api_path(first, second):
    """Return the shortest chain of exact reviewed API-only pairs, or None."""
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in (first, second)):
        return None
    pending = [[first]]
    seen = {first}
    pairs = (API_ONLY_PAIR, ACTIVITY_API_ONLY_PAIR, IMAGE_API_ONLY_PAIR, RUNNER_COMPAT_API_PAIR, UPLOAD_TX_API_PAIR, RELIABILITY_ADMISSION_API_PAIR, SOL400_API_PAIR, SOL_JPEG_API_PAIR, SOL_STREAM_API_PAIR, ADMIN_PREVIDEO_API_PAIR, ADMIN_DEEP_TRACE_API_PAIR)
    while pending:
        chain = pending.pop(0)
        if chain[-1] == second:
            return chain
        for pair in pairs:
            if chain[-1] in pair:
                for target in sorted(pair - seen):
                    seen.add(target)
                    pending.append(chain + [target])
    return None
