from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classic_video_evaluation import DIMENSIONS, summarize, worksheet  # noqa: E402


def test_unreviewed_is_not_a_pass_or_zero_cost_success():
    groups = summarize(worksheet())["groups"]
    assert len(groups) == 2
    for group in groups:
        assert group["reviewed"] == 0
        assert group["usableRateAmongReviewed"] is None
        assert group["costPerUsableClipUsd"] is None


def test_failed_attempt_cost_is_counted_and_unreviewed_is_not_hidden():
    row = worksheet()["samples"][0]
    row.update(generationStatus="succeeded", clipPath="observed.mp4", reviewer="operator",
               reviewed=True, usable=True, usableSeconds=7, durationSeconds=10,
               costUsd=1.0, dimensions=dict.fromkeys(DIMENSIONS, "pass"),
               observations=[{"atSeconds": 2, "note": "Full print is readable"}])
    failed = copy.deepcopy(row)
    failed.update(sampleId="failed", generationStatus="failed", reviewed=False, costUsd=0.5)
    data = {"samples": [row, failed]}
    result = summarize(data)["groups"][0]
    assert result["usableRateAmongReviewed"] == 1
    assert result["usableRateAllAttempts"] == 0.5
    assert result["costPerUsableClipUsd"] == 1.5
    failed.update(generationStatus="succeeded", costUsd=None)
    result = summarize(data)["groups"][0]
    assert result["usableRateAllAttempts"] is None
    assert result["costPerUsableClipUsd"] is None


def test_review_requires_actual_observation_and_valid_duration():
    data = worksheet()
    data["samples"][0].update(reviewed=True, usable=True)
    with pytest.raises(ValueError, match="Reviewed clips"):
        summarize(data)


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf")])
def test_invalid_costs_are_not_reported_as_valid(value):
    data = worksheet()
    data["samples"][0]["costUsd"] = value
    with pytest.raises(ValueError):
        summarize(data)
