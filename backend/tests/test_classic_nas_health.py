from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from libtv_agent.services import cutflow_observation as observation
from libtv_agent.services import cutflow_status_reader as reader
from libtv_agent.services import cutflow_multi_worker as worker


def test_probe_uses_existing_directories_and_leaves_no_files(tmp_path):
    for name in ("inbox", "outbox", "receipts/classic"):
        (tmp_path / name).mkdir(parents=True)
    settings = SimpleNamespace(cutflow_transport="lan", cutflow_handoff_root=tmp_path)
    assert observation.nas_probe(settings) == "OK"
    assert not any(p.is_file() for p in tmp_path.rglob("*"))
    (tmp_path / "outbox").rmdir()
    assert observation.nas_probe(settings) == "NAS_UNAVAILABLE"
    assert not (tmp_path / "outbox").exists()


@pytest.mark.parametrize("winerror, expected", [
    (1326, reader.AUTH_FAILED), (1219, reader.AUTH_FAILED),
    (5, reader.ACCESS_DENIED), (53, reader.UNREACHABLE),
])
def test_reader_classifies_windows_error(winerror, expected):
    error = OSError("secret")
    error.winerror = winerror
    assert reader.error_exit(error) == expected


def test_worker_preserves_auth_failure_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=reader.AUTH_FAILED, stdout=b""))
    with pytest.raises(worker.CutflowHandoffError) as error:
        worker._read_remote_snapshot(tmp_path, "outbox/job/status.json")
    assert error.value.code == "CUTFLOW_NAS_AUTH_FAILED"


def test_nas_health_alert_recovers_and_expires(tmp_path):
    settings = SimpleNamespace(artifact_root=tmp_path)
    now = datetime.now(UTC)
    assert observation.nas_health(settings, now)["code"] == "UNKNOWN"
    observation.save_nas_health(settings, "NAS_AUTH_FAILED", now)
    assert observation.nas_health(settings, now)["code"] == "NAS_AUTH_FAILED"
    observation.save_nas_health(settings, "OK", now + timedelta(seconds=60))
    assert observation.nas_health(settings, now + timedelta(seconds=61))["code"] == "OK"
    assert observation.nas_health(settings, now + timedelta(seconds=211))["code"] == "STALE"
