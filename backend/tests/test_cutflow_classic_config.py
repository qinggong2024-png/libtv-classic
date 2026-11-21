"""Validate classic configuration isolation without touching runtime credentials."""

import json
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "classic_cutflow.py"
load = runpy.run_path(str(SCRIPT))["cutflow_environment"]


def fixture_config(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = {
        "version": 1, "enabled": True, "baseUrl": "http://127.0.0.1:3001",
        "handoffRoot": str(tmp_path / "handoff"),
        "ootdTemplateId": "73afa4b6-028c-49cd-af57-f34cc4d99cf0",
        "autoSubmitAfter": None,
    }
    (runtime / "cutflow-service-token.txt").write_text("test-token-not-a-real-secret")
    return runtime / "cutflow-config.json", config


def test_absent_and_disabled_do_not_inherit_shell_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTFLOW_SERVICE_TOKEN", "unrelated-site-token")
    assert load(tmp_path) == {}
    path, config = fixture_config(tmp_path)
    config["enabled"] = False
    path.write_text(json.dumps(config))
    assert load(tmp_path) == {}


def test_cut_handoff_is_on_but_unattended_submit_is_off_by_default(tmp_path):
    path, config = fixture_config(tmp_path)
    path.write_text(json.dumps(config))
    environment = load(tmp_path)
    assert environment["CUTFLOW_AUTO_EDIT_ENABLED"] == "false"
    assert environment["CUTFLOW_SERVICE_TOKEN"] == "test-token-not-a-real-secret"
    assert "CUTFLOW_AUTO_SUBMIT_AFTER" not in environment
    assert all(key.startswith("CUTFLOW_") for key in environment)
    # A historical cutover timestamp is not permission to auto-submit.
    config["autoSubmitAfter"] = "2026-10-16T12:00:00+08:00"
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_AUTO_EDIT_ENABLED"] == "false"
    assert "CUTFLOW_AUTO_SUBMIT_AFTER" not in load(tmp_path)
    # A separately explicit opt-in is required if unattended CUT is ever authorized.
    config["autoSubmit"] = True
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_AUTO_EDIT_ENABLED"] == "true"
    assert load(tmp_path)["CUTFLOW_AUTO_SUBMIT_AFTER"] == config["autoSubmitAfter"]



@pytest.mark.parametrize(("field", "value"), [
    ("enabled", "true"), ("version", True), ("handoffRoot", "relative/path"),
    ("ootdTemplateId", "00000000-0000-0000-0000-000000000000"),
    ("baseUrl", "http://user:credential@host/"),
    ("baseUrl", "http://host/?token=secret"), ("autoSubmitAfter", "2026-10-16T12:00:00"),
    ("SESSION_SECRET", "injected-secret"), ("multiSourceEnabled", "true"),
    ("repeatReferenceEnabled", "true"),
    ("autoSubmit", "true"), ("autoSubmit", 1), ("autoSubmit", True),
])
def test_invalid_config_fails_without_exposing_values(tmp_path, field, value):
    path, config = fixture_config(tmp_path)
    config[field] = value
    path.write_text(json.dumps(config))
    with pytest.raises(SystemExit) as error:
        load(tmp_path)
    assert str(error.value) == (
        "Invalid classic CUT configuration; inspect local config/token files."
    )


def test_multi_source_flag_is_opt_in_and_absent_by_default(tmp_path):
    """The multi-source switch must be explicit: no config means closed."""
    path, config = fixture_config(tmp_path)
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_MULTI_SOURCE_ENABLED"] == "false"

    config["multiSourceEnabled"] = True
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_MULTI_SOURCE_ENABLED"] == "true"


def test_repeat_reference_is_opt_in(tmp_path):
    path, config = fixture_config(tmp_path)
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_REPEAT_REFERENCE_ENABLED"] == "false"

    config["repeatReferenceEnabled"] = True
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_REPEAT_REFERENCE_ENABLED"] == "true"


def test_missing_token_does_not_fallback_to_another_site(tmp_path, monkeypatch):
    path, config = fixture_config(tmp_path)
    path.write_text(json.dumps(config))
    path.with_name("cutflow-service-token.txt").unlink()
    monkeypatch.setenv("CUTFLOW_SERVICE_TOKEN", "unrelated-site-token")
    with pytest.raises(SystemExit):
        load(tmp_path)


@pytest.mark.parametrize("with_token", [False, True])
def test_lan_config_needs_no_template_and_can_reuse_local_channel_token(tmp_path, with_token):
    path, config = fixture_config(tmp_path)
    config["transport"] = "lan"
    config.pop("ootdTemplateId")
    if not with_token:
        path.with_name("cutflow-service-token.txt").unlink()
    path.write_text(json.dumps(config))
    environment = load(tmp_path)
    assert environment["CUTFLOW_TRANSPORT"] == "lan"
    assert environment["CUTFLOW_SERVICE_TOKEN"] == (
        "test-token-not-a-real-secret" if with_token else ""
    )
    assert environment["CUTFLOW_OOTD_TEMPLATE_ID"] == ""
    assert "CUTFLOW_AUTO_SUBMIT_AFTER" not in environment


def test_hybrid_config_is_explicit_and_keeps_old_default(tmp_path):
    path, config = fixture_config(tmp_path)
    path.write_text(json.dumps(config))
    assert load(tmp_path)["CUTFLOW_NAS_STATUS_ENABLED"] == "false"
    config.update(transport="lan", nasStatusEnabled=True, nasScanSeconds=60)
    path.write_text(json.dumps(config))
    environment = load(tmp_path)
    assert environment["CUTFLOW_NAS_STATUS_ENABLED"] == "true"
    assert environment["CUTFLOW_NAS_SCAN_SECONDS"] == "60"


@pytest.mark.parametrize("changes", [
    {"nasStatusEnabled": "true"}, {"nasScanSeconds": True}, {"nasScanSeconds": 0},
    {"nasScanSeconds": 301}, {"nasStatusEnabled": True, "transport": "bridge"},
])
def test_invalid_hybrid_config_fails_closed(tmp_path, changes):
    path, config = fixture_config(tmp_path)
    config.update(transport="lan")
    config.update(changes)
    path.write_text(json.dumps(config))
    with pytest.raises(SystemExit):
        load(tmp_path)
