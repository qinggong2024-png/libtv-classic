import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_online_standby as standby
import classic_green_api as green
import classic_blue_api as blue

OLD = "9b01a07df26943ac8f59970a89fd757db0acb7cbe0c021f175789b3b3261baf2"
ACTIVE = "800bb2238764b40f417c9ffe46bff06facc92ded2225915513121a011c20d3f4"
ACTIVITY = "c2c53ddf70c12ba45b9f21371b341fff2f0b5fa2418e30a1494d56e590c11844"


def test_only_reviewed_pairs():
    assert standby.reviewed_pair(ACTIVE, ACTIVITY)
    assert standby.reviewed_pair(ACTIVITY, ACTIVE)
    assert standby.reviewed_pair(OLD, ACTIVE)
    assert not standby.reviewed_pair(OLD, ACTIVITY)
    assert not standby.reviewed_pair(ACTIVE, "f" * 64)


def test_pinned_standby_preparation_keeps_existing_identity_check(monkeypatch, tmp_path):
    monkeypatch.setattr(green, "STORAGE", tmp_path)
    monkeypatch.setattr(green, "validated_release", lambda path, _: path)
    monkeypatch.setattr(blue, "supervisor_supports_blue", lambda _: True)
    calls = []
    def guard(*args, **kwargs):
        calls.append((args, kwargs))
        return {}, {}
    monkeypatch.setattr(standby, "guard", guard)
    assert green.replace_green_online(tmp_path, ACTIVITY, expected_active=ACTIVE,
                                      expected_standby=OLD)["action"] == "dry-run"
    assert calls[0][1] == {"expected_standby": OLD}
    with pytest.raises(RuntimeError, match="reviewed exact source pairs"):
        green.replace_green_online(tmp_path, ACTIVITY, expected_active=ACTIVE,
                                   expected_standby="f" * 64)
    assert len(calls) == 1
