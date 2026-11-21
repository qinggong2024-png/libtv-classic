"""Rule diagnostics expose only static identifiers, never arbitrary creative text."""
import pytest

from libtv_agent.services.ai_creative_planner import creative_validation_rule


@pytest.mark.parametrize("message,expected", [
    ("I native video prompt requires four explicit hard cuts", "NATIVE_HARD_CUT_COUNT"),
    ("I native contract requires five grounded beats", "NATIVE_SHOT_COUNT"),
    ("AI beats reorder storyboard source images", "STORYBOARD_REORDER"),
    ("beat introduces an unverified person in frame 2", "UNVERIFIED_PERSON"),
    ("solo/product frame contains an implied second person: 1; signal=person_reference", "SOLO_SECOND_PERSON"),
    ("AI plan lacks a visible product proof", "MISSING_PRODUCT_PROOF"),
    ("private creative containing unseen account details", "UNCLASSIFIED_VALIDATION"),
])
def test_only_static_rules_are_returned(message, expected):
    result = creative_validation_rule(ValueError(message))
    assert result == expected
    assert "private" not in result
