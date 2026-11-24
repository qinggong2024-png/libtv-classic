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
