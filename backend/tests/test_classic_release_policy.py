"""Fail-closed, offline checks for ALLWE Classic release completion."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    filename = Path(__file__).resolve().parents[2] / "scripts" / "classic_active_release_status.py"
    spec = importlib.util.spec_from_file_location("classic_active_release_status", filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APPROVED_API = "a" * 64
APPROVED_FRONT = "b" * 64
APPROVED_GIT = "c" * 40


def correct_state():
    return {
        "ok": True,
        "userUrl": "http://10.12.34.56:5176/",
        "activeApiPort": 8003,
        "standbyApiPort": 8002,
        "admissionPaused": False,
        "activeApi": {
            "sourceRevision": APPROVED_API,
            "diskMatchesRunning": True,
        },
        "mainCheckout": {
            "matchesRunning": True,
            "gitHead": APPROVED_GIT,
        },
        "frontendIndexSha256": APPROVED_FRONT,
        "galleryFilesDifferentFromMain": [],
    }


def check(state, **overrides):
    args = dict(
        expected_api_revision=APPROVED_API,
        expected_frontend_sha256=APPROVED_FRONT,
        expected_main_head=APPROVED_GIT,
    )
    args.update(overrides)
    return load().verify_release(state, **args)


def test_frozen_evidence_passes_only_when_entire_release_matches():
    assert check(correct_state()) == []


def test_internal_green_or_blue_slot_is_not_the_site_identity():
    state = correct_state()
    state["activeApiPort"], state["standbyApiPort"] = 8002, 8003
    assert check(state) == []


def test_release_rejects_alternative_port_and_changed_login_url():
    state = correct_state()
    state["activeApiPort"] = 8001
    state["userUrl"] = "http://10.12.34.56:8001/"
    errors = check(state)
    assert any("outside" in error for error in errors)
    assert any("5176" in error for error in errors)


def test_fails_when_source_main_or_frontend_drift():
    state = correct_state()
    state["activeApi"]["diskMatchesRunning"] = False
    state["mainCheckout"]["matchesRunning"] = False
    state["galleryFilesDifferentFromMain"] = ["backend/src/libtv_agent/api/admin.py"]
    state["frontendIndexSha256"] = "d" * 64
    errors = check(state)
    assert len(errors) >= 4


def test_release_does_not_accept_missing_approved_values():
    errors = check(correct_state(), expected_api_revision=None,
                   expected_frontend_sha256=None, expected_main_head=None)
    assert len(errors) == 3


def test_intake_must_be_reopened_after_cutover():
    state = correct_state()
    state["admissionPaused"] = True
    assert any("admission" in error for error in check(state))


def test_wrong_build_or_commit_does_not_masquerade_as_release():
    errors = check(correct_state(), expected_api_revision="9" * 64,
                   expected_main_head="f" * 40)
    assert len(errors) == 2


def test_site_url_is_portable_but_rejects_untrusted_or_wrong_ports():
    mod = load()
    assert mod.valid_classic_user_url("http://10.12.34.56:5176/")
    for bad in ("http://8.8.8.8:5176/", "http://127.0.0.1:5176/", "http://10.12.34.56:5173/", "http://x@10.12.34.56:5176/"):
        assert not mod.valid_classic_user_url(bad)


def test_site_url_checks_stack_identity(tmp_path):
    import json
    mod = load()
    runtime = tmp_path / "var" / "classic-lan" / "runtime"
    runtime.mkdir(parents=True)
    stack = {"root": str(tmp_path), "webPort": 5176, "lanIp": "10.12.34.56"}
    (runtime / "stack.json").write_text(json.dumps(stack), encoding="utf-8")
    assert mod.site_user_url(tmp_path) == "http://10.12.34.56:5176/"
    (runtime / "site-lan.json").write_text(json.dumps({"version": 1, "lanIp": "10.12.34.57"}), encoding="utf-8")
    assert mod.site_user_url(tmp_path) is None
