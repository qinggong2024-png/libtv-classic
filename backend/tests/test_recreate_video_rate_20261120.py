"""Unit checks for the 2026-11-20 Classic price change guard."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "apply_recreate_video_rate_20261120.py"
spec = importlib.util.spec_from_file_location("classic_recreate_price_change", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_approved_change_only():
    before = dict(module.BEFORE)
    after = module.proposal(before)
    assert before["recreateVideoPricePerSecondUsd"] == 200_000
    assert after["recreateVideoPricePerSecondUsd"] == 300_000
    assert {key: value for key, value in before.items() if key != "recreateVideoPricePerSecondUsd"} == {
        key: value for key, value in after.items() if key != "recreateVideoPricePerSecondUsd"
    }


def test_already_applied_is_idempotent():
    assert module.proposal(dict(module.AFTER)) is None


@pytest.mark.parametrize("different", [
    {"cutflowPriceUsd": 250_000},
    {"videoPricePerSecondUsd": 200_000},
    {"recreateVideoPricePerSecondUsd": 250_000},
    {"imagePriceUsd": None},
    {"unexpectedPriceUsd": 999_999},
])
def test_refuses_unapproved_or_partial_rates(different):
    prices = {**module.BEFORE, **different}
    with pytest.raises(ValueError, match="baseline differs"):
        module.proposal(prices)
