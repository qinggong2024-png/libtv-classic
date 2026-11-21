"""Offline reproduction of a failed official API task: never makes a paid call."""

import pytest
from libtv_agent.runner.supervisor import (
    _kling_risk_control_rejected,
    _provider_receipt_confirms_zero_billing,
)


def test_risk_control_only_matches_the_actual_kling_failure():
    assert _kling_risk_control_rejected("Failure to pass the risk control system")
    assert _kling_risk_control_rejected("failure TO PASS THE RISK CONTROL SYSTEM")
    assert not _kling_risk_control_rejected("Kling remote generation failed")
    assert not _kling_risk_control_rejected(None)


@pytest.mark.parametrize("charges", [
    [{"amount": "0"}],
    [{"amount": 0}],
    [{"amount": "0.00"}, {"amount": "0", "charge_type": "unit"}],
])
def test_explicit_complete_zero_receipt(charges):
    assert _provider_receipt_confirms_zero_billing({"billing": charges})


@pytest.mark.parametrize("charges", [
    None,
    [],
    [{"amount": "nan"}],
    [{"amount": "5"}],
    [{"amount": "0"}, {"amount": "1"}],
    [{"amount": "0"}, {"amount": "bad"}],
    [{"amount": True}],
    [{"other": "0"}],
    "0",
])
def test_missing_or_ambiguous_receipt_remains_unknown(charges):
    assert not _provider_receipt_confirms_zero_billing({"billing": charges})
