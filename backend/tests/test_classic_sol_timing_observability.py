"""Synthetic Sol timing/queue audit tests: no provider network or paid submissions."""
from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import CreativePlannerError
from libtv_agent.services.ootd_simple import plan_simple_ootd

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classic_sol_timing_audit.py"
FIRST = "An everyday clear outfit clip with print readable. CUT TO a natural closer print frame. " * 2
REWRITE = "Another distinct natural clothing movement with text fully readable. CUT TO the full outfit." * 2


class FakeResponses:
    def __init__(self, *outputs):
        self.outputs = outputs
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.outputs[len(self.calls) - 1]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(output_text=value)


def read_events(caplog, name):
    return [
        json.loads(record.getMessage().split(name + " ", 1)[1])
        for record in caplog.records if name + " " in record.getMessage()
    ]


async def plan(responses, *, recent_prompts=()):
    return await plan_simple_ootd(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="asset-secret", order=1, role="STORYBOARD",
            image_url="fixture:secret-image-url",
        )],
        product_images=[], creative_brief="secret-personal-brief",
        duration_seconds=10, recent_prompts=recent_prompts,
        client=SimpleNamespace(responses=responses),
    )


@pytest.mark.asyncio
async def test_no_history_one_request_and_skipped_review_without_prompt_leak(caplog):
    caplog.set_level(logging.INFO, logger="libtv_agent.services.ootd_simple")
    fake = FakeResponses(FIRST)
    result = await plan(fake)
    assert result[0].prompt == FIRST.strip()
    assert len(fake.calls) == 1
    stages = read_events(caplog, "SOL_STAGE_TIMING")
    outcomes = read_events(caplog, "SOL_VARIANT_RESULT")
    assert [x["stage"] for x in stages] == ["first_request"]
    assert stages[0]["outcome"] == "ok"
    assert stages[0]["elapsed_seconds"] >= 0
    assert outcomes[0]["review_result"] == "skipped"
    assert outcomes[0]["rewrite_result"] == "not_needed"
    assert "secret-personal-brief" not in caplog.text
    assert "secret-image-url" not in caplog.text
    assert FIRST[:40] not in caplog.text


@pytest.mark.asyncio
async def test_repeated_motion_only_rewrites_after_review_without_changing_calls(caplog):
    caplog.set_level(logging.INFO, logger="libtv_agent.services.ootd_simple")
    fake = FakeResponses(FIRST, "REPEAT", REWRITE)
    result = await plan(fake, recent_prompts=("Prior action sequence",))
    assert result[0].prompt == REWRITE.strip()
    assert len(fake.calls) == 3
    assert [x["stage"] for x in read_events(caplog, "SOL_STAGE_TIMING")] == [
        "first_request", "motion_review", "rewrite",
    ]
    assert len(read_events(caplog, "SOL_VARIANT_RESULT")) == 1
    result_event = read_events(caplog, "SOL_VARIANT_RESULT")[0]
    assert result_event["review_result"] == "repeat"
    assert result_event["rewrite_result"] == "used"
    assert "Prior action sequence" not in caplog.text


@pytest.mark.asyncio
async def test_different_review_does_not_trigger_rewrite(caplog):
    caplog.set_level(logging.INFO, logger="libtv_agent.services.ootd_simple")
    fake = FakeResponses(FIRST, "DIFFERENT")
    result = await plan(fake, recent_prompts=("earlier prompt",))
    assert result[0].prompt == FIRST.strip()
    assert len(fake.calls) == 2
    assert [x["stage"] for x in read_events(caplog, "SOL_STAGE_TIMING")] == [
        "first_request", "motion_review",
    ]
    event = read_events(caplog, "SOL_VARIANT_RESULT")[0]
    assert event["review_result"] == "different"
    assert event["rewrite_result"] == "not_needed"


@pytest.mark.asyncio
async def test_motion_review_failure_keeps_first_draft_and_records_error(caplog):
    caplog.set_level(logging.INFO, logger="libtv_agent.services.ootd_simple")
    fake = FakeResponses(FIRST, RuntimeError("isolated synthetic error"))
    result = await plan(fake, recent_prompts=("earlier prompt",))
    assert result[0].prompt == FIRST.strip()
    assert len(fake.calls) == 2
    assert read_events(caplog, "SOL_STAGE_TIMING")[-1]["outcome"] == "error"
    assert read_events(caplog, "SOL_VARIANT_RESULT")[0]["review_result"] == "failed"


@pytest.mark.asyncio
async def test_first_request_failure_still_recorded(caplog):
    caplog.set_level(logging.INFO, logger="libtv_agent.services.ootd_simple")
    fake = FakeResponses(RuntimeError("fake no network"))
    with pytest.raises(CreativePlannerError):
        await plan(fake)
    assert len(fake.calls) == 1
    assert read_events(caplog, "SOL_STAGE_TIMING")[0]["outcome"] == "error"
    assert read_events(caplog, "SOL_VARIANT_RESULT") == []


def audit_module():
    spec = importlib.util.spec_from_file_location("classic_sol_timing_audit_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_auditor_filters_window_and_reads_only_safe_stage_fields(tmp_path):
    audit = audit_module()
    log_dir = tmp_path / "var" / "classic-lan" / "logs"
    log_dir.mkdir(parents=True)
    now = datetime.now(UTC)
    event_time = now.isoformat(timespec="milliseconds")
    valid = [
        {"at_utc": event_time, "stage": "first_request", "variant": 1,
         "outcome": "ok", "elapsed_seconds": 12.5},
        {"at_utc": event_time, "stage": "motion_review", "variant": 1,
         "outcome": "ok", "elapsed_seconds": 2.0},
        {"at_utc": event_time, "stage": "rewrite", "variant": 1,
         "outcome": "ok", "elapsed_seconds": 8.1},
    ]
    records = [
        "INFO SOL_STAGE_TIMING " + json.dumps(item) for item in valid
    ]
    records.append("INFO SOL_VARIANT_RESULT " + json.dumps({
        "at_utc": event_time, "variant": 1,
        "review_result": "repeat", "rewrite_result": "used",
    }))
    records.append('INFO SOL_STAGE_TIMING {"stage":"first_request","outcome":"ok"}')
    records.append("INFO SOL_STAGE_TIMING " + json.dumps({
        **valid[0], "at_utc": (now - timedelta(days=2)).isoformat(),
    }))
    (log_dir / "runner.log").write_text("\n".join(records) + "\n", encoding="utf-8")
    result = audit.collect_logs(tmp_path, cutoff=now - timedelta(hours=1), max_bytes=100000)
    assert result["stages"]["first_request"]["count"] == 1
    assert result["stages"]["first_request"]["p90_seconds"] == 12.5
    assert result["stages"]["motion_review"]["count"] == 1
    assert result["stages"]["rewrite"]["count"] == 1
    assert result["review_results"] == {"repeat": 1}
    assert result["rewrite_results"] == {"used": 1}
    assert result["review_invocation_rate_of_completed_variants"] == 1
    assert result["rewrite_adoption_rate"] == 1
    assert result["log_coverage"]["complete_window_guaranteed"] is False


def test_historical_pressure_is_sampled_not_claimed_exact(tmp_path):
    audit = audit_module()
    db = tmp_path / "test.db"
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    def date(minutes):
        return (now - timedelta(minutes=minutes)).replace(tzinfo=None).isoformat()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE parent_task (workflow_kind TEXT, created_at TEXT, "
            "analysis_started_at TEXT, analysis_heartbeat_at TEXT, "
            "analysis_stage TEXT, phase TEXT)"
        )
        connection.execute(
            "CREATE TABLE generation_attempt (provider TEXT, task_id TEXT, "
            "submitted_at TEXT, process_started_at TEXT, completed_at TEXT, status TEXT)"
        )
        connection.execute(
            "INSERT INTO parent_task VALUES ('FIXED_TEMPLATE', ?, ?, ?, "
            "'COMPLETED', 'DELIVERABLE')",
            (date(40), date(25), date(19)),
        )
        connection.execute(
            "INSERT INTO parent_task VALUES (\'FIXED_TEMPLATE\', ?, NULL, NULL, "
            "\'COMPLETED\', \'ACTION_REQUIRED\')",
            (date(6000),),
        )
        connection.execute(
            "INSERT INTO generation_attempt VALUES "
            "('kling_official_api','synthetic-handle', ?, NULL, ?, 'SUCCEEDED')",
            (date(24), date(10)),
        )
    output = audit.queue_pressure(db, hours=1, cloud_limit=20, now=now)
    assert output["sampling_minutes"] == 5
    assert output["analysis_queued_max"] == 1  # ancient never-started task is not queued
    assert output["local_cloud_occupancy_max"] == 1
    assert output["samples_with_cloud_spare_slots_and_analysis_queued"] >= 1
    assert "approximation" in output["interpretation"] or "snapshots" in output["interpretation"]
