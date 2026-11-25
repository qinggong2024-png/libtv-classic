"""Path classification must never silently treat paid pipeline changes as CSS."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from classic_release_classifier import classify_changes, classify_path, committed_changes  # noqa: E402


@pytest.mark.parametrize(("paths", "mode"), [
    (["frontend/src/features/admin/adminCenter.css",
      "frontend/src/features/admin/AdminCenterView.tsx",
      "frontend/src/features/admin/AdminCenterView.test.tsx"], "frontend-hot"),
    (["frontend/public/logo.svg"], "frontend-hot"),
    (["frontend/src/api.ts"], "api-canary-review"),
    (["frontend/src/domain/taskCenterState.ts"], "api-canary-review"),
    (["backend/src/libtv_agent/api/admin.py"], "api-canary-review"),
    (["backend/src/libtv_agent/services/cutflow_multi_worker.py"], "worker-handoff-required"),
    (["backend/alembic/versions/01_add_column.py"], "maintenance-review"),
    (["backend/src/libtv_agent/services/video_prompt_builder.py"], "prompt-approval-required"),
    (["frontend/src/features/production/promptEditor.tsx"], "prompt-approval-required"),
    (["scripts/classic_api_hot_switch.mjs"], "manual-review"),
    (["docs/README.md", "backend/tests/test_release.py"], "no-runtime-change"),
    (["frontend/src/App.tsx", "backend/src/libtv_agent/services/runner.py"], "worker-handoff-required"),
    (["frontend/src/App.tsx", "scripts/something.py"], "manual-review"),
    (["scripts/classic_runner.py", "scripts/classic_release.py"], "manual-review"),
    (["frontend/src/App.tsx", ".env"], "manual-review"),
])
def test_conservative_release_categories(paths, mode):
    result = classify_changes(paths)
    assert result["mode"] == mode
    assert result["frontendHotEligible"] == (mode == "frontend-hot")
    if any("runner" in path or "cutflow" in path or "worker" in path for path in paths):
        assert result["requiresQuiescence"]


@pytest.mark.parametrize("path", ["../frontend/src/App.tsx", "/frontend/App.tsx",
                                   "frontend\\src\\App.tsx", "frontend/src/backdoor.exe"])
def test_invalid_paths_never_enter_hot_release(path):
    assert classify_path(path) == "unknown"


def test_committed_diff_rejects_equal_base_and_unrelated_commit(monkeypatch, tmp_path):
    from classic_release_classifier import _git
    # This test uses the real local Git repo, not the production database.
    head = _git(ROOT, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(ValueError, match="differ"):
        committed_changes(ROOT, head, head)
    with pytest.raises(ValueError, match="Invalid release"):
        committed_changes(ROOT, "this-is-not-a-commit")


def test_admin_prevideo_exact_api_pair():
    from classic_release_classifier import ADMIN_PREVIDEO_API_PAIR, reviewed_pair, reviewed_api_path
    a, b = sorted(ADMIN_PREVIDEO_API_PAIR)
    assert reviewed_pair(a, b) and reviewed_pair(b, a)
    assert reviewed_api_path(a, b) == [a, b]
    assert not reviewed_pair(a, "0" * 64)
