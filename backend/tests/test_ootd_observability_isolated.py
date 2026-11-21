"""Offline-only regression: optional wire audit and non-gating human review."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from libtv_agent.adapters.kling_official_api import KlingOfficialApiAdapter
from libtv_agent.adapters.kling_ootd_audit import (
    audit_without_blocking,
    submission_snapshot,
)
from libtv_agent.adapters.video_provider import VideoGenerationRequest
from libtv_agent.config import Settings
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classic_ootd_quality_review.py"
spec = importlib.util.spec_from_file_location("quality_cli_isolated", SCRIPT)
assert spec and spec.loader
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


def _image(path: Path) -> Path:
    Image.new("RGB", (320, 320), "white").save(path)
    return path


def _adapter(tmp_path: Path) -> KlingOfficialApiAdapter:
    return KlingOfficialApiAdapter(Settings(
        app_env="test", kling_api_enabled=True, kling_api_key="offline-only",
        kling_api_model="kling-3.0-omni",
        runner_root=tmp_path, artifact_root=tmp_path / "artifacts", _env_file=None,
    ))


@pytest.mark.asyncio
async def test_dynamic_prompt_is_unchanged_even_when_legacy_converter_enabled(tmp_path):
    prompt = (
        "10秒、9:16竖屏。同一人同一件T恤。"
        "0.0–3.0秒｜首帧自然转身。"
        "3.0–6.2秒｜直接切镜，正面走两步。"
        "6.2–10.0秒｜直接切镜，印花完整入镜。"
    )
    request = VideoGenerationRequest(
        prompt=prompt, model="kling-3.0-omni", image_paths=(
            _image(tmp_path / "one.png"), _image(tmp_path / "two.png")),
        product_reference_paths=(_image(tmp_path / "product.png"),),
        duration=10, multi_shot=True, aspect_ratio="9:16", audio="off",
        external_task_id="offline-audit-1",
        director_native_profile="allwe-ootd-sol-dynamic-display-v1",
    )
    adapter = _adapter(tmp_path)
    payload = await adapter.build_request_payload(request)
    baseline = json.dumps(payload, sort_keys=True)
    audit = submission_snapshot(request, payload)
    assert audit["nativeShotConversion"] == "profile_not_in_native_allowlist"
    assert audit["preAdapterPrompt"] == audit["exactKlingPrompt"] == prompt
    assert audit["promptChanged"] is False
    assert [x["id"] for x in audit["imageRoles"]] == ["image_1", "image_2", "image_3"]
    assert all(len(x["sha256"]) == 64 for x in audit["imageRoles"])
    assert [x["intentRole"] for x in audit["imageRoles"]] == [
        "STORYBOARD_START", "STORYBOARD", "PRODUCT",
    ]
    assert audit["settings"]["multi_shot"] is True
    assert baseline == json.dumps(payload, sort_keys=True)


@pytest.mark.asyncio
async def test_native_formatter_audit_is_only_observational(tmp_path):
    source = (
        "镜头一0-3秒：自然展示衣服；"
        "镜头二3-6秒：印花近景；"
        "镜头三6-10秒：完整穿搭"
    )
    request = VideoGenerationRequest(
        prompt=source, model="kling-3.0-omni",
        image_paths=(_image(tmp_path / "one.png"),),
        product_reference_paths=(_image(tmp_path / "product.png"),),
        duration=10, multi_shot=True, external_task_id="offline-audit-2",
        director_native_profile="allwe-ootd-sol-simple-v1",
    )
    payload = await _adapter(tmp_path).build_request_payload(request)
    audit = submission_snapshot(request, payload)
    assert audit["nativeShotConversion"] == "converted_to_native_shots"
    assert audit["promptChanged"]
    assert audit["exactKlingPrompt"].startswith("shot 1, 3,")
    assert audit["preAdapterPrompt"] == source


def test_audit_opt_in_and_write_errors_never_gate(monkeypatch, tmp_path):
    data = {"externalTaskId": "safe-id", "submissionState": "WIRE_PREPARED"}
    monkeypatch.delenv("ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED", raising=False)
    audit_without_blocking(tmp_path, data)
    assert not (tmp_path / "ootd-wire-audit").exists()
    monkeypatch.setenv("ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED", "1")
    audit_without_blocking(tmp_path, data)
    saved = list((tmp_path / "ootd-wire-audit").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["submissionState"] == "WIRE_PREPARED"
    monkeypatch.setattr("libtv_agent.adapters.kling_ootd_audit.os.replace",
                        lambda *_: (_ for _ in ()).throw(PermissionError("test")))
    audit_without_blocking(tmp_path, data)  # must not raise


@pytest.mark.asyncio
async def test_submit_records_real_payload_and_provider_receipt_without_network(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED", "1")
    adapter = _adapter(tmp_path)
    req = VideoGenerationRequest(
        prompt="10秒真实穿搭，直接切镜，展示原始印花。",
        model="kling-3.0-omni",
        image_paths=(_image(tmp_path / "first.png"),),
        product_reference_paths=(_image(tmp_path / "product.png"),),
        duration=10, multi_shot=True, external_task_id="test-receipt-only",
        director_native_profile="allwe-ootd-sol-dynamic-display-v1",
    )
    posts = []

    async def fake_post(method, endpoint, body, **kwargs):
        posts.append((method, endpoint, body, kwargs))
        return {"data": {"id": "local-test-provider-id", "status": "processing"}}

    monkeypatch.setattr(adapter, "_request_json", fake_post)
    result = await adapter.submit(req)
    assert result.provider_task_id == "local-test-provider-id"
    assert len(posts) == 1
    assert posts[0][0] == "POST"
    records = list((tmp_path / "ootd-wire-audit").glob("*.json"))
    assert len(records) == 1
    audit = json.loads(records[0].read_text(encoding="utf-8"))
    assert audit["submissionState"] == "PROVIDER_ACCEPTED"
    assert audit["providerTaskId"] == result.provider_task_id
    assert audit["exactKlingPrompt"] == posts[0][2]["contents"][0]["text"]
    assert audit["preAdapterPrompt"] == req.prompt
    assert audit["promptChanged"] is False


def _sample_db(path: Path, parent_id: str, variant_id: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE parent_task(id TEXT, phase TEXT);
        CREATE TABLE generation_variant(id TEXT,parent_task_id TEXT,variant_index INTEGER,
          prompt_hash TEXT,generation_status TEXT,validation_status TEXT,
          download_status TEXT,generation_config_json TEXT);
        CREATE TABLE generation_attempt(id TEXT,variant_id TEXT,task_id TEXT,
          status TEXT,provider TEXT,provider_model TEXT,credits_consumed INTEGER,
          local_output_path TEXT,completed_at TEXT,created_at TEXT);
        CREATE TABLE download_artifact(id TEXT,attempt_id TEXT,sha256 TEXT,
          duration_seconds INTEGER,size_bytes INTEGER,width INTEGER,height INTEGER,
          created_at TEXT);
        """)
        conn.execute("INSERT INTO parent_task VALUES(?,?)", (parent_id, "DELIVERABLE"))
        conn.execute(
            "INSERT INTO generation_variant VALUES(?,?,?,?,?,?,?,?)",
            (variant_id, parent_id, 1, "sha", "SUCCEEDED", "PASSED", "READY",
             '{"directorNativeProfile":"allwe-ootd-sol-dynamic-display-v1"}'),
        )
        conn.execute(
            "INSERT INTO generation_attempt VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("a", variant_id, "provider-id", "SUCCEEDED", "kling-api",
             "kling-3.0-omni", 8, "local.mp4", "2026-11-20T00:00:00", "2026-11-20T00:00:00"),
        )
        conn.execute(
            "INSERT INTO download_artifact VALUES(?,?,?,?,?,?,?,?)",
            ("d", "a", "video-sha", 10, 100, 1080, 1920, "2026-11-20T00:00:00"),
        )


def test_quality_record_starts_unreviewed_and_never_updates_db(tmp_path):
    parent_id = uuid.uuid4().hex
    variant_id = uuid.uuid4().hex
    db = tmp_path / "sample.db"
    _sample_db(db, parent_id, variant_id)
    before = db.read_bytes()
    manifest = quality.create_manifest(db, parent_id)
    row = manifest["variants"][0]
    assert row["reviewVerdict"] == "UNREVIEWED"
    assert row["technicalValidation"] == "PASSED"
    assert row["costUnits"] == 8
    quality.append_issue(
        manifest, variant_id, code="TEXT_CROPPED", severity="HIGH",
        start=6.0, end=9.0, note="lower text cropped",
    )
    with pytest.raises(ValueError, match="Cannot PASS"):
        quality.finish_review(manifest, variant_id, verdict="PASS", reviewer="QA")
    quality.finish_review(manifest, variant_id, verdict="FAIL", reviewer="QA")
    assert manifest["variants"][0]["reviewVerdict"] == "FAIL"
    assert manifest["impactsCut"] is False
    assert manifest["impactsGeneration"] is False
    assert db.read_bytes() == before


def test_classic_startup_audit_requires_an_explicit_local_flag(
    tmp_path, monkeypatch,
):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    lan_spec = importlib.util.spec_from_file_location(
        "classic_lan_audit_gate", scripts / "classic_lan.py",
    )
    assert lan_spec and lan_spec.loader
    lan = importlib.util.module_from_spec(lan_spec)
    lan_spec.loader.exec_module(lan)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    secret = runtime / "session-secret.txt"
    secret.write_text("only-a-fixture")
    monkeypatch.setattr(lan, "STORAGE", tmp_path)
    monkeypatch.setattr(lan, "SECRET_FILE", secret)
    monkeypatch.setattr(lan, "generation_authorized", lambda: False)
    monkeypatch.setattr(lan, "child_environment", lambda *_: {})
    monkeypatch.setattr(lan, "cutflow_environment", lambda *_: {})
    assert lan.lan_environment(8002, 5176, "127.0.0.1")[
        "ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED"
    ] == "0"
    (runtime / "enable-ootd-wire-audit.flag").touch()
    assert lan.lan_environment(8002, 5176, "127.0.0.1")[
        "ALLWE_OOTD_SUBMISSION_AUDIT_ENABLED"
    ] == "1"
