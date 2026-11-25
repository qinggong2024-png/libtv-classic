"""Independent frontend release tests use temporary Git and static trees only."""
import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_isolated_frontend as mod


def commit(root, message):
    mod.git(root, "add", ".")
    mod.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)
    return mod.git(root, "rev-parse", "HEAD")


@pytest.fixture
def release(tmp_path, monkeypatch):
    root = tmp_path / "main"
    root.mkdir()
    mod.git(root, "init", "-b", "main")
    (root / ".gitignore").write_text("var/\nfrontend/dist/\nfrontend/dist-build/\n")
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "src" / "App.tsx").write_text("old")
    (root / "backend").mkdir()
    (root / "backend" / "api.py").write_text("active")
    base = commit(root, "live")
    (root / "backend" / "api.py").write_text("pending")
    (root / "frontend" / "src" / "App.tsx").write_text("new")
    main = commit(root, "mixed main")
    candidate = tmp_path / "candidate"
    mod.git(root, "worktree", "add", "--detach", str(candidate), base)
    (candidate / "frontend" / "src" / "App.tsx").write_text("new")
    candidate_head = commit(candidate, "approved frontend only")
    dist = root / "frontend" / "dist"
    dist.mkdir()
    entry = dist / "index.html"
    entry.write_bytes(b"old")
    old = mod.digest(entry)
    api = "a" * 64
    mod.record_receipt(root, {"candidateHead": base, "frontendNew": old, "servedFrontendSha256": old})
    def live(_):
        return {"ok": True, "userUrl": "http://10.12.34.56:5176/", "activeApiPort": 8002,
                "mainCheckout": {"gitHead": main, "matchesRunning": False},
                "activeApi": {"sourceRevision": api, "diskMatchesRunning": True,
                              "gitHead": base, "root": str(tmp_path / "running-api")},
                "activeConfigRevision": "config", "activeSchemaRevision": "schema",
                "admissionPaused": False, "frontendIndexSha256": mod.digest(entry)}
    monkeypatch.setattr(mod, "report", live)
    monkeypatch.setattr(mod, "workspace_source_revision", lambda _: api)
    monkeypatch.setattr(mod, "http_digest", lambda path="/": mod.digest(entry if path == "/" else dist / path.lstrip("/")))
    pins = dict(base=base, approved=main, main=main, api=api, old=old, candidate_head=candidate_head)
    return root, candidate, pins


def test_exact_reviewed_status_presentation_scope_is_pinned(monkeypatch):
    api = "ad6d7b8ed034520060f8f9df71ae9dde35e1e018c037a7ed05215a198d4a8edb"
    trees = {"base": "7b84c98f36b42b52c261c376cecb6f3d4544318c",
             "approved": "e6117fd78ea61caa10bd164d9934058a18dd745d"}
    monkeypatch.setattr(mod, "tree", lambda root, commit: trees[commit])
    monkeypatch.setattr(mod, "committed_changes", lambda root, base, approved: {
        "base": base, "target": approved,
        "paths": ["frontend/src/domain/taskCenterState.ts", "frontend/src/features/production/TaskMediaOverview.tsx"]})
    assert not mod.frontend_scope(None, "base", "approved")["frontendHotEligible"]
    assert not mod.frontend_scope(None, "base", "approved", api="a" * 64)["frontendHotEligible"]
    approved = mod.frontend_scope(None, "base", "approved", api=api)
    assert approved["frontendHotEligible"]
    assert approved["exactPresentationReview"] == "classic-status-20261124"
    trees["approved"] = "f" * 40
    assert not mod.frontend_scope(None, "base", "approved", api=api)["frontendHotEligible"]


def test_isolated_frontend_is_not_blocked_by_unrelated_main_api(release):
    root, candidate, pins = release
    result = mod.assess_isolated(root, candidate, **pins)
    assert result["ready"], result
    assert result["warnings"] == ["UNRELATED_MAIN_API_DRIFT"]
    assert result["changes"]["excludedMainPaths"] == ["backend/api.py"]


def test_refuses_dirty_candidate_or_api_fingerprint_mismatch(release, monkeypatch):
    root, candidate, pins = release
    (candidate / "backend" / "api.py").write_text("different")
    monkeypatch.setattr(mod, "workspace_source_revision", lambda _: "b" * 64)
    result = mod.assess_isolated(root, candidate, **pins)
    assert not result["ready"]
    assert "CANDIDATE_CHANGED_OR_DIRTY" in result["blockers"]
    assert "CANDIDATE_API_FINGERPRINT_MISMATCH" in result["blockers"]


def test_refuses_candidate_with_unreleased_backend_even_if_fingerprint_mock_matches(release):
    root, candidate, pins = release
    (candidate / "backend" / "api.py").write_text("different")
    pins["candidate_head"] = commit(candidate, "not frontend only")
    result = mod.assess_isolated(root, candidate, **pins)
    assert "CANDIDATE_NON_FRONTEND_DIFFERS_FROM_ACTIVE" in result["blockers"]


def test_refuses_missing_receipt_or_changed_live_index(release):
    root, candidate, pins = release
    for receipt in mod.history(root).glob("*.json"):
        receipt.unlink()
    assert "LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED" in mod.assess_isolated(root, candidate, **pins)["blockers"]
    (root / "frontend" / "dist" / "index.html").write_bytes(b"changed")
    assert "FRONTEND_BASELINE_CHANGED" in mod.assess_isolated(root, candidate, **pins)["blockers"]


def prepare_stage(candidate, monkeypatch):
    real_run = subprocess.run
    def build(args, **kwargs):
        if args[0] == "npm-test":
            stage = candidate / "frontend" / "dist-build"
            (stage / "assets").mkdir(parents=True, exist_ok=True)
            (stage / "assets" / "index-new.js").write_bytes(b"new asset")
            (stage / "index.html").write_text('<script src="/assets/index-new.js"></script>')
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "npm-test")
    monkeypatch.setattr(mod.subprocess, "run", build)


def test_publish_installs_assets_and_records_verified_receipt(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    result = mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), **pins)
    assert result["ready"] and result["frontendPublished"]
    assert result["frontendNew"] == result["servedFrontendSha256"]
    assert Path(result["rollbackIndex"]).read_bytes() == b"old"
    assert mod.receipt_for(root, result["frontendNew"], pins["approved"], active=result)
    assert (root / "frontend" / "dist" / "assets" / "index-new.js").read_bytes() == b"new asset"


def test_build_is_staged_before_production_lock(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)

    @contextmanager
    def lock(_):
        assert (candidate / "frontend/dist-build/index.html").exists()
        assert (root / "frontend/dist/index.html").read_bytes() == b"old"
        yield

    result = mod.publish_isolated(root, candidate, lock=lock, **pins)
    assert result["ready"] and result["frontendPublished"]


def test_failed_http_asset_check_restores_our_old_index(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    real_http = mod.http_digest
    monkeypatch.setattr(mod, "http_digest", lambda path="/": "bad" if path.startswith("/assets/") else real_http(path))
    with pytest.raises(RuntimeError, match="HTTP_ASSET_MISMATCH"):
        mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), **pins)
    assert (root / "frontend" / "dist" / "index.html").read_bytes() == b"old"


def test_concurrent_frontend_switch_blocks_before_index_write(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    original = mod.assess_isolated
    calls = 0
    def assess(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "frontend" / "dist" / "index.html").write_bytes(b"other release")
        return original(*args, **kwargs)
    monkeypatch.setattr(mod, "assess_isolated", assess)
    result = mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), **pins)
    assert not result["ready"]
    assert (root / "frontend" / "dist" / "index.html").read_bytes() == b"other release"


def test_immutable_asset_collision_blocks_without_overwriting(tmp_path):
    stage, dist = tmp_path / "stage", tmp_path / "dist"
    for folder, data in ((stage, b"new"), (dist, b"old")):
        (folder / "assets").mkdir(parents=True)
        (folder / "assets" / "same.js").write_bytes(data)
        (folder / "index.html").write_text('<script src="/assets/same.js"></script>')
    with pytest.raises(ValueError, match="IMMUTABLE_ASSET_COLLISION"):
        mod.staged_files(stage, dist)
    assert (dist / "assets" / "same.js").read_bytes() == b"old"



def test_prepare_builds_fresh_candidate_without_touching_main(release):
    root, candidate, pins = release
    new_path = candidate.parent / "fresh"
    result = mod.prepare_isolated(root, new_path, **{k: v for k, v in pins.items() if k != "candidate_head"})
    assert result["ready"], result
    assert mod.git(root, "rev-parse", "HEAD") == pins["main"]
    assert (new_path / "backend" / "api.py").read_text() == "active"
    assert (new_path / "frontend" / "src" / "App.tsx").read_text() == "new"
    with pytest.raises(ValueError, match="PATH_MUST_BE_NEW"):
        mod.prepare_isolated(root, new_path, **{k: v for k, v in pins.items() if k != "candidate_head"})


def test_frontend_api_contract_diff_still_blocks(release):
    root, candidate, pins = release
    for worktree in (root, candidate):
        (worktree / "frontend" / "src" / "api.ts").write_text("new API contract")
    pins["main"] = pins["approved"] = commit(root, "contract change")
    pins["candidate_head"] = commit(candidate, "contract candidate")
    assert not mod.frontend_scope(root, pins["base"], pins["approved"])["frontendHotEligible"]


def test_reviewed_main_can_advance_during_build_without_rebuilding(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    old_report = mod.report
    old_run = mod.subprocess.run
    def build(args, **kwargs):
        result = old_run(args, **kwargs)
        if args[0] == "npm-test":
            (root / "backend/api.py").write_text("new unrelated backend")
            commit(root, "other session backend commit")
        return result
    def live(path):
        state = old_report(path)
        state["mainCheckout"]["gitHead"] = mod.git(root, "rev-parse", "HEAD")
        return state
    monkeypatch.setattr(mod.subprocess, "run", build)
    monkeypatch.setattr(mod, "report", live)
    result = mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), allow_main_advance=True, **pins)
    assert result["ready"] and result["frontendPublished"]
    assert result["requestedMainHead"] == pins["main"]
    assert result["mainHead"] != pins["main"]


def test_main_pin_never_advances_over_new_frontend(release):
    root, candidate, pins = release
    (root / "frontend/src/App.tsx").write_text("unreviewed")
    commit(root, "other UI")
    assert mod.refresh_main_pin(root, pins) == pins


def test_main_pin_advances_backend_only_with_explicit_older_approved_frontend(release):
    root, _, pins = release
    # The operator deliberately selects an older frontend while another UI
    # change is already integrated. A backend-only fast-forward is harmless.
    requested = dict(pins, approved=pins["base"])
    (root / "backend/api.py").write_text("unrelated backend")
    head = commit(root, "backend only")
    assert mod.refresh_main_pin(root, requested) == dict(requested, main=head)


def test_public_svg_line_endings_preserve_verified_live_bytes(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    stage = candidate / "frontend/dist-build"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "favicon.svg").write_bytes(b"<svg>\r\n</svg>")
    live = root / "frontend/dist/favicon.svg"
    live.write_bytes(b"<svg>\n</svg>")
    mod.record_receipt(root, {"candidateHead": pins["base"], "frontendNew": pins["old"],
                              "servedFrontendSha256": pins["old"],
                              "artifactFileDigests": {"favicon.svg": mod.digest(live)}})
    result = mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), **pins)
    assert result["ready"] and result["preservedPublicSvg"] == ["favicon.svg"]
    assert live.read_bytes() == b"<svg>\n</svg>"


def test_public_svg_actual_content_change_is_not_normalized(release, monkeypatch):
    root, candidate, pins = release
    prepare_stage(candidate, monkeypatch)
    stage = candidate / "frontend/dist-build"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "favicon.svg").write_bytes(b"<svg>changed</svg>")
    live = root / "frontend/dist/favicon.svg"
    live.write_bytes(b"<svg>original</svg>")
    with pytest.raises(ValueError, match="PUBLIC_FILE_CHANGE_REQUIRES_REVIEW"):
        mod.publish_isolated(root, candidate, lock=lambda _: nullcontext(), **pins)
    assert live.read_bytes() == b"<svg>original</svg>"



def test_feedback_build_input_is_pinned_to_live_receipt(release, monkeypatch):
    root, candidate, pins = release
    value = "https://example.invalid/feedback"
    digest = hashlib.sha256(value.encode()).hexdigest()
    receipt = mod.record_receipt(root, {"candidateHead": pins["base"], "frontendNew": pins["old"],
        "servedFrontendSha256": pins["old"], "buildInputDigests": {"VITE_FEEDBACK_URL": digest}})
    config = root / "var/classic-lan/runtime/frontend-build-env.json"
    config.write_text(json.dumps({"VITE_FEEDBACK_URL": value}))
    monkeypatch.setenv("VITE_FEEDBACK_URL", "https://wrong.invalid/")
    env, hashes = mod.frontend_build_inputs(root, Path(receipt).name)
    assert env["VITE_FEEDBACK_URL"] == value and hashes == {"VITE_FEEDBACK_URL": digest}
    config.unlink()
    with pytest.raises(ValueError, match="FRONTEND_BUILD_INPUT_CHANGED"):
        mod.frontend_build_inputs(root, Path(receipt).name)
    config.write_text(json.dumps({"VITE_FEEDBACK_URL": "https://changed.invalid/"}))
    with pytest.raises(ValueError, match="FRONTEND_BUILD_INPUT_CHANGED"):
        mod.frontend_build_inputs(root, Path(receipt).name)


def test_candidate_promotion_is_pinned_and_source_only(release, monkeypatch):
    from classic_simple_deploy import promote_candidate
    import classic_active_release_status as active_status
    import classic_release as release_module
    root, candidate, pins = release
    monkeypatch.setattr(release_module, "report", mod.report)
    monkeypatch.setattr(active_status, "workspace_source_revision", lambda _: pins["api"])
    head = mod.git(root, "rev-parse", "HEAD")
    fresh = root.parent / "integration"
    mod.git(root, "worktree", "add", "--detach", str(fresh), head)
    (fresh / "frontend/src/App.tsx").write_text("reviewed")
    target = commit(fresh, "candidate")
    args = dict(expected_main=head, expected_candidate=target)
    monkeypatch.setattr(active_status, "workspace_source_revision", lambda _: "b" * 64)
    assert "CANDIDATE_BACKEND_NOT_ACTIVE_KEEP_IN_CANDIDATE" in promote_candidate(root, fresh, **args)["blockers"]
    monkeypatch.setattr(active_status, "workspace_source_revision", lambda _: pins["api"])
    assert promote_candidate(root, fresh, **args)["ready"]
    assert not promote_candidate(root, fresh, apply=True, **args)["ready"]
    assert mod.git(root, "rev-parse", "HEAD") == head
    (fresh / "frontend/src/App.tsx").write_text("unreviewed")
    assert not promote_candidate(root, fresh, apply=True, reviewed=True, **args)["ready"]
    (fresh / "frontend/src/App.tsx").write_text("reviewed")
    result = promote_candidate(root, fresh, apply=True, reviewed=True, **args)
    assert result["sourcePromoted"] and not result["deployed"]
    assert mod.git(root, "rev-parse", "HEAD") == target
    assert (root / "frontend/dist/index.html").read_bytes() == b"old"
    assert not promote_candidate(root, fresh, apply=True, reviewed=True, **args)["ready"]



def test_deep_video_exact_presentation_review_is_pinned(monkeypatch):
    api = "68333d6b064d48c62c995a449fa53cd2620ebfdde85b552c143ea1dfc9a8bd61"
    trees = {"base": "e6117fd78ea61caa10bd164d9934058a18dd745d",
             "approved": "cccbed2c7a74453cb9806c69aeaef7c3538d4f72"}
    monkeypatch.setattr(mod, "tree", lambda root, commit: trees[commit])
    monkeypatch.setattr(mod, "committed_changes", lambda *args: {"base": "base", "target": "approved",
        "paths": ["frontend/src/domain/taskTimingPresentation.ts"]})
    assert mod.frontend_scope(None, "base", "approved", api=api)["exactPresentationReview"] == "classic-deep-video-20261125"
    assert not mod.frontend_scope(None, "base", "approved", api="a" * 64)["frontendHotEligible"]
    trees["approved"] = "0" * 40
    assert not mod.frontend_scope(None, "base", "approved", api=api)["frontendHotEligible"]


def test_public_svg_continuity_receipt_reuses_same_artifact_evidence(release, monkeypatch):
    root, candidate, pins = release
    stage = candidate / "frontend/dist-build"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "favicon.svg").write_bytes(b"<svg>\r\n</svg>")
    live = root / "frontend/dist/favicon.svg"
    live.write_bytes(b"<svg>\n</svg>")
    sha = mod.digest(live)
    monkeypatch.setattr(mod, "http_digest", lambda path="/": sha if path == "/favicon.svg" else pins["old"])
    original = mod.record_receipt(root, {
        "candidateHead": pins["base"], "frontendNew": pins["old"],
        "servedFrontendSha256": pins["old"], "artifactFileDigests": {"favicon.svg": sha},
    })
    continuity = {"mode": "api-only-compatibility", "frontendPublished": False,
                  "frontendOld": pins["old"], "frontendNew": pins["old"],
                  "servedFrontendSha256": pins["old"], "artifactSourceHead": pins["base"],
                  "priorReceipt": Path(original).name}
    first = mod.record_receipt(root, continuity)
    second = mod.record_receipt(root, dict(continuity, priorReceipt=Path(first).name))
    assert mod.preserve_verified_public_svg(root, stage, live.parent, Path(second).name) == ["favicon.svg"]
    assert (stage / "favicon.svg").read_bytes() == live.read_bytes()
    (stage / "favicon.svg").write_bytes(b"<svg>\r\n</svg>")
    wrong = mod.record_receipt(root, dict(continuity, artifactSourceHead="f"*40))
    assert mod.preserve_verified_public_svg(root, stage, live.parent, Path(wrong).name) == []
    wrong = mod.record_receipt(root, dict(continuity, frontendNew="f"*64, servedFrontendSha256="f"*64, frontendOld="f"*64))
    assert mod.preserve_verified_public_svg(root, stage, live.parent, Path(wrong).name) == []
    wrong = mod.record_receipt(root, dict(continuity, priorReceipt="../outside.json"))
    assert mod.preserve_verified_public_svg(root, stage, live.parent, Path(wrong).name) == []
