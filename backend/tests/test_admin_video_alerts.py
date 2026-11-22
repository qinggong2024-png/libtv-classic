"""Deterministic admin health labels never invent content-quality conclusions."""
from datetime import UTC, datetime, timedelta

from libtv_agent.services.admin_video_alerts import classify_saved_videos
from libtv_agent.services.admin_video_review import _add_cut_state


def _row(number: int, seconds: int, mode: str = "reference_guided") -> dict:
    submitted = datetime(2026, 11, 20, 10, tzinfo=UTC)
    completed = submitted + timedelta(seconds=seconds)
    return {
        "artifact_id": f"artifact-{number}",
        "attempt_id": f"attempt-{number}",
        "provider": "kling",
        "generation_config": {
            "directorMode": mode,
            "referenceImageBindings": [{"assetId": "a", "imageId": "image_1"}],
        },
        "submitted_at": submitted,
        "completed_at": completed,
        "confirmed_at": submitted - timedelta(seconds=10),
        "created_at": completed + timedelta(seconds=12),
        "validation_status": "PASSED",
        "final_approval_status": "PENDING",
    }


def test_outlier_requires_a_comparable_history_and_only_flags_observed_stage():
    rows = [_row(i, 100) for i in range(20)]
    rows.append(_row(20, 900))
    result = classify_saved_videos(rows, {}, {})
    assert all(item["health"]["severity"] == "normal" for item in result[:20])
    outlier = result[-1]
    assert outlier["health"]["severity"] == "warning"
    assert [item["code"] for item in outlier["health"]["reasons"]] == ["SLOW_PROVIDER"]
    assert "21 条可比较记录" in outlier["health"]["primary"]["detail"]
    assert all(
        "穿帮" not in finding["title"]
        for item in result for finding in item["health"]["reasons"]
    )


def test_missing_evidence_is_not_misrepresented_as_cloud_failure():
    row = _row(1, 120)
    row["submitted_at"] = None
    row["generation_config"]["referenceImageBindings"] = []
    result = classify_saved_videos([row], {}, {})[0]
    assert result["health"]["severity"] == "warning"
    assert {r["code"] for r in result["health"]["reasons"]} == {
        "GENERATION_TIMES_MISSING", "BINDINGS_MISSING",
    }


def test_latest_cut_status_overrides_older_failure_and_only_current_failure_is_critical():
    earlier = datetime(2026, 11, 20, tzinfo=UTC)
    latest = {}
    _add_cut_state(latest, "artifact-1", {
        "status": "FAILED", "source": "multi_cutflow_task.status",
        "created_at": earlier, "updated_at": earlier,
    })
    _add_cut_state(latest, "artifact-1", {
        "status": "PENDING_REVIEW", "source": "multi_cutflow_task.status",
        "created_at": earlier + timedelta(hours=2),
        "updated_at": earlier + timedelta(hours=2),
    })
    row = _row(1, 100)
    result = classify_saved_videos([row], {}, latest)[0]
    assert result["health"]["severity"] == "normal"
    assert result["health"]["qualityStatus"] == "not_reviewed"
    latest["artifact-1"]["status"] = "FAILED"
    result = classify_saved_videos([_row(1, 100)], {}, latest)[0]
    assert result["health"]["severity"] == "critical"
    assert result["health"]["primary"]["code"] == "CUT_FAILED"



def test_filtered_page_keeps_severity_against_full_30day_cohort():
    baseline = [_row(i, 100) for i in range(20)]
    outlier = _row(21, 900)
    baseline.append(outlier)
    one = classify_saved_videos([_row(21, 900)], {}, {}, baseline_rows=baseline)
    assert one[0]["health"]["severity"] == "warning"
    assert one[0]["health"]["primary"]["code"] == "SLOW_PROVIDER"

def test_provider_modes_have_separate_baselines():
    rows = [_row(i, 100) for i in range(20)]
    rows.append(_row(21, 900, mode="cross_scene_v2"))
    result = classify_saved_videos(rows, {}, {})
    assert result[-1]["health"]["severity"] == "normal"
