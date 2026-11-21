"""Non-sensitive classification for classic CUT NAS failures."""
import pytest
from libtv_agent.runner.cutflow_worker import storage_failure_code


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (1326, "NAS_AUTH_FAILED"),
        (5, "NAS_ACCESS_DENIED"),
        (65, "NAS_ACCESS_DENIED"),
        (53, "NAS_UNREACHABLE"),
        (67, "NAS_UNREACHABLE"),
        (1231, "NAS_UNREACHABLE"),
        (None, "OSError"),
    ],
)
def test_nas_failure_classification_does_not_leak_paths(code, expected):
    error = OSError("sensitive path or account identifier must not be logged")
    if code is not None:
        error.winerror = code
    result = storage_failure_code(error)
    assert result == expected
    assert "sensitive" not in result


def test_non_io_cut_coord_error_is_also_sanitized():
    assert storage_failure_code(ValueError("receipt had private data")) == "ValueError"
