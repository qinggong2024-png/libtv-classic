"""GPT-6.2 Sol rollout gates: no changed production video-provider selection."""

import pytest

from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    _effective_creative_reasoning_effort,
    production_v25_enabled,
)
from libtv_agent.services.native_five_shot import native_i_eligible
from libtv_agent.services.native_v34_director import native_v34_eligible
from libtv_agent.services.ootd_simple import (
    eligible as simple_ootd_eligible,
    _director_effort,
)


@pytest.mark.parametrize(("model", "configured", "expected"), [
    ("gpt-6.2-sol", "medium", "medium"),
    ("gpt-6.2-sol", "none", "medium"),
    ("gpt-6.2-sol", "high", "high"),
    ("gpt-6.1-sol", "none", "low"),
])
def test_actual_ootd_request_effort(model, configured, expected):
    assert _director_effort(Settings(
        app_env="test", openai_model=model,
        openai_reasoning_effort=configured, _env_file=None,
    )) == expected


@pytest.mark.parametrize(("model", "configured", "expected"), [
    ("gpt-6.2-sol", "none", "medium"),
    ("gpt-6.2-sol", "medium", "medium"),
    ("gpt-6.2-sol", "low", "low"),
    ("gpt-6.1-sol", "none", "low"),
    ("gpt-6.1-terra", "none", "none"),
])
def test_reasoning_effort_stays_compatible(model, configured, expected):
    assert _effective_creative_reasoning_effort(model, configured) == expected


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6.2-sol"])
def test_existing_director_routes_remain_eligible(model):
    assert simple_ootd_eligible(
        generation_mode="automatic", cut_mode="explicit_cuts",
        provider="api", model="kling-3.0-omni", ai_model=model, duration=10,
    )
    assert native_v34_eligible(
        enabled=True, generation_mode="automatic", cut_mode="explicit_cuts",
        provider="cli", model="kling-video-v3_0_omni", ai_model=model, duration=10,
    )
    assert native_i_eligible(
        enabled=True, generation_mode="automatic", cut_mode="explicit_cuts",
        provider="cli", model="kling-video-v3_0_omni", ai_model=model, duration=10,
    )
    assert production_v25_enabled(Settings(
        app_env="test", openai_model=model, ai_creative_v25_enabled=True,
        _env_file=None,
    ))


def test_new_sol_does_not_silently_enable_unsupported_video_model():
    assert not simple_ootd_eligible(
        generation_mode="automatic", cut_mode="explicit_cuts",
        provider="api", model="kling-4.0-flash",
        ai_model="gpt-6.2-sol", duration=10,
    )
