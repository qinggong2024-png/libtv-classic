"""Offline rollout and token-accounting contract; never contacts a provider."""
from types import SimpleNamespace

import pytest
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    CreativeDirectorResponse,
    CreativePlannerError,
    _parse_provider,
    production_v25_enabled,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


@pytest.mark.parametrize(
    ("model", "enabled", "expected"),
    [
        ("gpt-6.1-sol", False, False),
        ("gpt-6.1-sol", True, True),
        ("GPT-6.1-TERRA", True, True),
        ("gpt-5.6-luna", True, False),
    ],
)
def test_production_v25_is_explicitly_gated(model, enabled, expected):
    settings = Settings(
        app_env="test", openai_model=model,
        ai_creative_v25_enabled=enabled, _env_file=None,
    )
    assert production_v25_enabled(settings) is expected


@pytest.mark.asyncio
async def test_legacy_nonstream_exposes_reasoning_usage_without_content(caplog):
    source = CreativeDirectorResponse(
        observations=fixture_solo_facts(),
        variants=fixture_solo_plan().variants,
    )

    class FakeResponses:
        def __init__(self):
            self.calls = []

        async def parse(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                id="response-test-id",
                status="completed",
                output_parsed=source,
                output_text='{"private":"not for logs"}',
                usage=SimpleNamespace(
                    input_tokens=110, output_tokens=970,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=730),
                ),
            )

    client = SimpleNamespace(responses=FakeResponses())
    settings = Settings(
        app_env="test", openai_model="gpt-6.1-sol", _env_file=None,
    )
    with caplog.at_level("INFO"):
        result = await _parse_provider(
            settings, [{"role": "user", "content": "synthetic"}],
            CreativeDirectorResponse, max_tokens=4300, client=client,
        )
    assert isinstance(result, CreativeDirectorResponse)
    assert len(client.responses.calls) == 1
    assert "outputTokens=970 reasoningTokens=730 nonReasoningTokens=240" in caplog.text
    assert "maxOutputTokens=4300" in caplog.text
    assert "response-test-id" in caplog.text
    assert "not for logs" not in caplog.text


@pytest.mark.asyncio
async def test_legacy_incomplete_output_records_limit_without_replaying(caplog):
    class FakeResponses:
        def __init__(self):
            self.calls = 0

        async def parse(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output_parsed=None,
                output_text="",
                usage=SimpleNamespace(
                    input_tokens=280, output_tokens=4300,
                    output_tokens_details={"reasoning_tokens": 4200},
                ),
            )

    client = SimpleNamespace(responses=FakeResponses())
    with caplog.at_level("INFO"):
        with pytest.raises(CreativePlannerError) as caught:
            await _parse_provider(
                Settings(app_env="test", _env_file=None),
                [{"role": "user", "content": "synthetic"}],
                CreativeDirectorResponse, max_tokens=4300, client=client,
            )
    assert caught.value.code == "AI_CREATIVE_RESPONSE_INVALID"
    assert "incompleteReason=max_output_tokens" in caplog.text
    assert "reasoningTokens=4200 nonReasoningTokens=100" in caplog.text
    assert client.responses.calls == 1
