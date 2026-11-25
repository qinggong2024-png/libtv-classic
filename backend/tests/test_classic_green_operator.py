import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_green_operator as operator
import classic_green_api as green


def test_isolated_caller_delegates_exact_pins(monkeypatch):
    monkeypatch.setattr(operator, "needs_independent", lambda: True)
    seen = []
    monkeypatch.setattr(operator, "dispatch", lambda args: seen.append(args) or {"action": "prepared"})
    monkeypatch.setattr(green, "validated_release", lambda path, _: path)
    result = green.restore_vacant_green_online(Path("candidate"), "b"*64,
                      expected_active="a"*64, expected_previous="c"*64, apply=True)
    assert result == {"action": "prepared"}
    assert seen == [["restore-vacant-online", "--release-root", "candidate",
                    "--expected-source-revision", "b"*64, "--expected-active-revision", "a"*64,
                    "--expected-standby-revision", "c"*64, "--baseline-unknown", "15", "--apply"]]


def test_normal_user_bootstrap_uses_existing_wmi_and_pins(monkeypatch, tmp_path):
    monkeypatch.setattr(operator, "STORAGE", tmp_path)
    monkeypatch.setattr(operator.autostart, "is_elevated", lambda: False)
    seen = []
    monkeypatch.setattr(operator, "powershell", lambda script, payload, timeout: seen.append((script,payload)) or "123")
    operator.launch_wmi("a"*32, ["recover"])
    assert "Win32_Process" in seen[0][0]
    assert "classic_green_operator.py" in seen[0][1]["command"]
    assert "recover" in seen[0][1]["command"]
    with pytest.raises(ValueError):
        operator.command("../bad", ["recover"])


def test_execute_receipt_only_after_independent_context(monkeypatch, tmp_path):
    (tmp_path/"runtime").mkdir()
    monkeypatch.setattr(operator, "STORAGE", tmp_path)
    monkeypatch.setattr(operator, "needs_independent", lambda: True)
    with pytest.raises(RuntimeError):
        operator.execute("a"*32, ["recover"])
    assert not operator.result_path("a"*32).exists()
    monkeypatch.setattr(operator, "needs_independent", lambda: False)
    monkeypatch.setattr(green, "main", lambda: print(json.dumps({"recovered": True})))
    assert operator.execute("a"*32, ["recover"])
    assert json.loads(operator.result_path("a"*32).read_text())["result"] == {"recovered": True}
