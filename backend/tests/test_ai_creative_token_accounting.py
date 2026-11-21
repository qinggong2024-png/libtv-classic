"""No provider calls: response-ID and over-cap telemetry must be safe."""
from types import SimpleNamespace

import pytest
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    _parse_streaming_response,
    _safe_provider_response_id,
)
from libtv_agent.services.creative_plan_v25 import WireCreativeResponse
from test_ai_creative_v25 import wire_fixture


class Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def __aiter__(self):
        async def events():
            yield SimpleNamespace(type="codex.response.metadata")
            yield SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp_from_created_123"),
            )
            yield SimpleNamespace(type="response.output_text.delta")
            yield SimpleNamespace(type="response.completed", response=self.response)
        return events()


class Client:
    def __init__(self, output_tokens, response_id):
        self.requests = []
        self.response = SimpleNamespace(
            id=response_id,
            status="completed",
            output_text=wire_fixture().model_dump_json(),
            usage=SimpleNamespace(
                input_tokens=9080,
                output_tokens=output_tokens,
                output_tokens_details=SimpleNamespace(reasoning_tokens=227),
            ),
        )
        self.responses = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return Stream(self.response)


@pytest.mark.asyncio
async def test_over_budget_usage_warns_with_safe_response_id_and_no_retry(caplog):
    client = Client(4850, "resp_real_opaque_123")
    with caplog.at_level("INFO"):
        result = await _parse_streaming_response(
            client,
            Settings(app_env="test", openai_model="gpt-6.1-terra", _env_file=None),
            [{"role": "user", "content": "internal test; no network"}],
            WireCreativeResponse,
            4600,
        )
    assert isinstance(result, WireCreativeResponse)
    assert len(client.requests) == 1
    assert client.requests[0]["max_output_tokens"] == 4600
    assert "provider usage exceeded requested cap" in caplog.text
    assert "excessTokens=250" in caplog.text
    assert "responseId=resp_real_opaque_123" in caplog.text
    assert "usageOverCap=True" in caplog.text
    assert "firstDeltaSeconds=" in caplog.text
    assert "internal test; no network" not in caplog.text
    assert wire_fixture().variants[0].idea not in caplog.text


@pytest.mark.asyncio
async def test_created_event_fallback_id_and_no_false_warning(caplog):
    client = Client(1500, None)
    with caplog.at_level("INFO"):
        result = await _parse_streaming_response(
            client, Settings(app_env="test", _env_file=None),
            [{"role": "user", "content": "synthetic"}],
            WireCreativeResponse, 4600,
        )
    assert isinstance(result, WireCreativeResponse)
    assert "responseId=resp_from_created_123" in caplog.text
    assert "usageOverCap=False" in caplog.text
    assert "exceeded requested cap" not in caplog.text


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), ("valid_01:x", "valid_01:x"), ("x\ncontent", None),
     ("a" * 129, None), ("私有", None), ("includes space", None)],
)
def test_opaque_response_id_is_log_safe(value, expected):
    assert _safe_provider_response_id(value) == expected
