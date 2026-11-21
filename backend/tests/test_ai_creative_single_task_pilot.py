"""Single existing-task Sol V2.5 pilot selection; no provider or video calls."""

from types import SimpleNamespace

import pytest
from libtv_agent.config import Settings
from libtv_agent.services.fixed_video_tasks import (
    SOL_V25_PILOT_MARKER,
    _task_specific_v25_enabled,
)


def task(marker=None, workflow_kind="FIXED_TEMPLATE"):
    return SimpleNamespace(
        workflow_kind=workflow_kind,
        settings_json={"_aiCreativeV25Pilot": marker} if marker else {},
    )


@pytest.mark.parametrize(
    ("model", "marker", "task_exists", "workflow_kind", "expected"),
    [
        ("gpt-6.1-sol", SOL_V25_PILOT_MARKER, True, "FIXED_TEMPLATE", True),
        ("gpt-6.1-sol", None, True, "FIXED_TEMPLATE", False),
        ("gpt-6.1-sol", SOL_V25_PILOT_MARKER, False, "FIXED_TEMPLATE", False),
        ("gpt-6.1-sol", SOL_V25_PILOT_MARKER, True, "DEEP_VIDEO", False),
        ("gpt-6.1-terra", SOL_V25_PILOT_MARKER, True, "FIXED_TEMPLATE", False),
        ("gpt-5.6-luna", SOL_V25_PILOT_MARKER, True, "FIXED_TEMPLATE", False),
    ],
)
def test_single_task_sol_pilot_is_strictly_scoped(
    model,
    marker,
    task_exists,
    workflow_kind,
    expected,
):
    settings = Settings(
        app_env="test",
        openai_model=model,
        ai_adapter_mode="openai",
        ai_creative_v25_enabled=False,
        _env_file=None,
    )
    existing = task(marker, workflow_kind) if task_exists else None
    assert _task_specific_v25_enabled(settings, existing) is expected


def test_single_task_opt_in_does_not_change_normal_task_default():
    settings = Settings(
        app_env="test",
        openai_model="gpt-6.1-sol",
        ai_creative_v25_enabled=False,
        _env_file=None,
    )
    assert not _task_specific_v25_enabled(settings, None)
    assert not _task_specific_v25_enabled(settings, task("arbitrary-user-supplied-flag"))
