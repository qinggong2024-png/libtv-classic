import subprocess
import sys
import time
from pathlib import Path

import pytest
from libtv_agent.services import cutflow_multi_worker as worker
from libtv_agent.services.cutflow_handoff import CutflowHandoffError


def test_isolated_snapshot_read_and_missing(tmp_path):
    folder = tmp_path / "outbox" / "job"
    folder.mkdir(parents=True)
    path = folder / "status.json"
    path.write_bytes(b'{"status":"ACCEPTED"}')
    assert worker._read_remote_snapshot(
        tmp_path, "outbox/job/status.json"
    ) == b'{"status":"ACCEPTED"}'
    assert worker._read_remote_snapshot(tmp_path, "outbox/job/missing.json") is None
    with pytest.raises(CutflowHandoffError) as caught:
        worker._read_remote_snapshot(tmp_path, "../status.json")
    assert caught.value.code == "UNSAFE_PATH"
    path.write_bytes(b"x" * (worker.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(CutflowHandoffError) as caught:
        worker._read_remote_snapshot(tmp_path, "outbox/job/status.json")
    assert caught.value.code == "INVALID_RESULT"


def test_blocked_read_is_bounded_without_worker_exit(tmp_path, monkeypatch):
    original = subprocess.run

    def slow_reader(args, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    monkeypatch.setattr(worker.subprocess, "run", slow_reader)
    start = time.monotonic()
    with pytest.raises(CutflowHandoffError) as caught:
        worker._read_remote_snapshot(Path(tmp_path), "outbox/job/status.json", timeout=0.25)
    assert caught.value.code == "CUTFLOW_NAS_UNAVAILABLE"
    assert time.monotonic() - start < 4
