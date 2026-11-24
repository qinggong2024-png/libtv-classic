"""Read-only planning uses the actual live receipt and never relaxes strict gates."""
from test_classic_isolated_frontend import release, mod
import classic_release as cli


def proven_receipt(root, pins, **overrides):
    item = dict(artifactSourceHead=pins["base"], approvedFrontendHead=pins["base"],
                frontendNew=pins["old"], servedFrontendSha256=pins["old"],
                activeApiRevision=pins["api"], activeApiPort=8002,
                activeConfigRevision="config", activeSchemaRevision="schema")
    item.update(overrides)
    mod.record_receipt(root, item)


def test_plan_selects_actual_live_ancestor_and_ignores_stale_api_receipt(release, monkeypatch):
    root, _, pins = release
    proven_receipt(root, pins)
    proven_receipt(root, pins, activeApiRevision="b" * 64)
    monkeypatch.setattr(cli, "report", mod.report)
    result = cli.plan_live_frontend(root)
    assert result["ready"]
    assert result["fullMainApiMatches"] is False
    assert result["baseline"]["base"] == pins["base"]
    assert result["nextAction"] == "review-api-compatibility-and-prepare-isolated"
    assert pins["old"] in result["prepareArgs"]


def test_plan_recognizes_already_live_frontend_despite_backend_drift(release, monkeypatch):
    root, _, pins = release
    proven_receipt(root, pins, artifactSourceHead=pins["candidate_head"],
                   approvedFrontendHead=pins["main"])
    monkeypatch.setattr(cli, "report", mod.report)
    result = cli.plan_live_frontend(root)
    assert result["ready"]
    assert result["nextAction"] == "frontend-already-current"
    assert not result["fullMainApiMatches"]


def test_missing_current_receipt_and_http_mismatch_fail_closed(release, monkeypatch):
    root, _, pins = release
    monkeypatch.setattr(cli, "report", mod.report)
    assert "LIVE_FRONTEND_SOURCE_RECEIPT_REQUIRED" in cli.plan_live_frontend(root)["blockers"]
    proven_receipt(root, pins)
    monkeypatch.setattr(mod, "http_digest", lambda: "bad")
    assert not cli.plan_live_frontend(root)["ready"]


def test_receipts_are_unique_even_when_clock_is_frozen(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from pathlib import Path
    import json

    class FrozenClock:
        @staticmethod
        def now(_):
            return datetime(2026, 11, 24, tzinfo=UTC)

    monkeypatch.setattr(mod, "datetime", FrozenClock)
    first = Path(mod.record_receipt(tmp_path, {"one": 1}))
    second = Path(mod.record_receipt(tmp_path, {"two": 2}))
    assert first != second
    assert json.loads(first.read_text(encoding="utf-8")) == {"one": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"two": 2}
    assert not list(mod.history(tmp_path).glob(".receipt-*"))


def test_dirty_main_blocks_plan(release, monkeypatch):
    root, _, pins = release
    proven_receipt(root, pins)
    monkeypatch.setattr(cli, "report", mod.report)
    (root / "frontend" / "src" / "App.tsx").write_text("uncommitted")
    assert "PRODUCTION_MAIN_CHANGED_OR_DIRTY" in cli.plan_live_frontend(root)["blockers"]


def historical_publication(release, monkeypatch):
    root, candidate, pins = release
    from classic_release_classifier import IMAGE_API_ONLY_PAIR, RUNNER_COMPAT_API_PAIR
    middle = next(iter(IMAGE_API_ONLY_PAIR & RUNNER_COMPAT_API_PAIR))
    old = next(iter(IMAGE_API_ONLY_PAIR - {middle}))
    target = next(iter(RUNNER_COMPAT_API_PAIR - {middle}))
    dist = root / "frontend" / "dist"
    (dist / "assets").mkdir()
    (dist / "assets" / "old.js").write_bytes(b"original asset")
    files = {"index.html": pins["old"], "assets/old.js": mod.digest(dist / "assets/old.js")}
    state = mod.report(root)
    state["activeApi"]["sourceRevision"] = target
    monkeypatch.setattr(mod, "report", lambda _: state)
    monkeypatch.setattr(cli, "report", lambda _: state)
    proven_receipt(root, pins, activeApiRevision=old, ready=True, frontendPublished=True,
                   artifactFilesVerified=len(files), artifactFileDigests=files)
    return root, state, files, [old, middle, target]


def test_recovers_only_verified_publication_across_reviewed_api_chain(release, monkeypatch):
    root, state, files, chain = historical_publication(release, monkeypatch)
    assert mod.live_frontend_baseline(root, state) is None
    before = set(mod.history(root).glob("*.json"))
    preview = cli.plan_live_frontend(root)
    assert preview["ready"] and preview["receiptRecoveryRequired"]
    assert preview["baseline"]["reviewedApiPath"] == chain
    assert set(mod.history(root).glob("*.json")) == before
    proof = mod.recover_frontend_receipt(root, state, apply=True)
    assert proof["compatibilityRecovered"]
    assert mod.live_frontend_baseline(root, state)["receipt"] == proof["receipt"]
    assert not cli.plan_live_frontend(root)["receiptRecoveryRequired"]


def test_recovery_refuses_unknown_api_schema_or_corrupt_assets(release, monkeypatch):
    root, state, files, chain = historical_publication(release, monkeypatch)
    assert mod.recover_frontend_receipt(root, dict(state, activeSchemaRevision="different")) is None
    assert mod.recover_frontend_receipt(root, dict(state, activeApi=dict(state["activeApi"], sourceRevision="f"*64))) is None
    (root / "frontend/dist/assets/old.js").write_bytes(b"modified")
    assert mod.recover_frontend_receipt(root, state) is None


def test_recovery_refuses_http_drift_and_changed_live_identity(release, monkeypatch):
    root, state, files, chain = historical_publication(release, monkeypatch)
    original = mod.http_digest
    monkeypatch.setattr(mod, "http_digest", lambda path="/": "f"*64 if path.startswith("/assets/") else original(path))
    assert mod.recover_frontend_receipt(root, state) is None
    monkeypatch.setattr(mod, "http_digest", original)
    monkeypatch.setattr(mod, "report", lambda _: dict(state, activeApiPort=8003))
    assert mod.recover_frontend_receipt(root, state, apply=True) is None


def test_approved_ancestor_does_not_include_later_main_frontend(release, monkeypatch):
    root, _, pins = release
    proven_receipt(root, pins)
    from test_classic_isolated_frontend import commit
    (root / "frontend/src/App.tsx").write_text("other session new UI")
    later = commit(root, "unrelated frontend")
    state = mod.report(root)
    state["mainCheckout"]["gitHead"] = later
    monkeypatch.setattr(cli, "report", lambda _: state)
    plan = cli.plan_live_frontend(root, approved=pins["main"])
    assert plan["ready"] and plan["mainHead"] == later
    assert plan["approvedFrontendHead"] == pins["main"]
    assert plan["changes"]["target"] == pins["main"]


def test_frontend_plan_and_publish_gate_allow_only_verified_read_path(release, monkeypatch):
    root, candidate, pins = release
    proven_receipt(root, pins)
    state = mod.report(root)
    state.update(ok=False, frontendReady=True, frontendWarnings=["SQLITE_WRITE_PROBE_UNAVAILABLE_READ_PATH_VERIFIED"])
    monkeypatch.setattr(cli, "report", lambda _: state)
    monkeypatch.setattr(mod, "report", lambda _: state)
    plan = cli.plan_live_frontend(root)
    assert plan["ready"] and not plan["liveHealthy"] and plan["frontendReady"]
    result = mod.assess_isolated(root, candidate, **pins)
    assert result["ready"] and "SQLITE_WRITE_PROBE_UNAVAILABLE_READ_PATH_VERIFIED" in result["warnings"]
    state["frontendReady"] = False
    assert not cli.plan_live_frontend(root)["ready"]
    assert not mod.assess_isolated(root, candidate, **pins)["ready"]
