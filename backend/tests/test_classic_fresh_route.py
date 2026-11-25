"""Cold startup routing must not silently change a deployed instance."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import classic_lan  # noqa: E402


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    storage = tmp_path / "classic-lan"
    path = storage / "runtime"
    path.mkdir(parents=True)
    monkeypatch.setattr(classic_lan, "STORAGE", storage)
    monkeypatch.setattr(classic_lan, "STATE", path / "stack.json")
    return path


def test_new_instance_initializes_primary_route(runtime):
    classic_lan.initialize_fresh_route(8002)
    assert json.loads((runtime / "api-routing.json").read_text()) == {
        "version": 1, "activeApiPort": 8002,
    }


def test_existing_route_is_never_overwritten(runtime):
    route = runtime / "api-routing.json"
    route.write_text('{"version":1,"activeApiPort":8003}', encoding="utf-8")
    before = route.read_bytes()
    classic_lan.initialize_fresh_route(8002)
    assert route.read_bytes() == before


@pytest.mark.parametrize("marker", ["stack.json", "green-api.json", "blue-api-pin.json", "../classic.db"])
def test_missing_route_existing_instance_fails_closed(runtime, marker):
    (runtime / marker).touch()
    with pytest.raises(SystemExit, match="Existing classic instance"):
        classic_lan.initialize_fresh_route(8002)
    assert not (runtime / "api-routing.json").exists()


def test_site_address_persists_after_stack_state_removed(runtime):
    classic_lan.remember_lan_ip("10.12.34.56")
    assert classic_lan.configured_lan_ip() == "10.12.34.56"
    with pytest.raises(SystemExit, match="Existing site address differs"):
        classic_lan.remember_lan_ip("10.12.34.57")
    assert classic_lan.configured_lan_ip() == "10.12.34.56"


def test_existing_stack_address_is_read_without_site_file(runtime):
    classic_lan.STATE.write_text(json.dumps({"lanIp": "10.12.34.56"}), encoding="utf-8")
    assert classic_lan.configured_lan_ip() == "10.12.34.56"


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "8.8.8.8", "::1"])
def test_invalid_saved_site_address_rejected(runtime, address):
    (runtime / "site-lan.json").write_text(json.dumps({"version": 1, "lanIp": address}), encoding="utf-8")
    with pytest.raises(ValueError):
        classic_lan.configured_lan_ip()
