"""Offline AI planner gateway diagnosis and no-local-review behavior tests."""

from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    CreativeDirectorResponse,
    CreativePlannerError,
    plan_creative_video,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


@pytest.mark.asyncio
async def test_gateway_400_records_only_safe_upstream_diagnostics(caplog):
    error = UpstreamHTTPError(400)
    error.body = {
        "error": {
            "type": "invalid_request_error",
            "code": "unsupported_parameter",
            "param": "reasoning.effort",
            "message": "reasoning.effort is unsupported; sk-test-secret and user photo leaked?",
        }
    }
    error.request_id = "req_diagnostic1234"
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as raised:
            await invoke(SequenceResponses([error]))
    result = raised.value
    assert result.code == "AI_CREATIVE_REQUEST_REJECTED"
    assert result.upstream_diagnostics == {
        "error_type": "invalid_request_error",
        "error_code": "unsupported_parameter",
        "error_param": "reasoning.effort",
        "request_id": "req_diagnostic1234",
        "hint": "reasoning_parameter",
    }
    assert "upstreamParam=reasoning.effort" in caplog.text
    assert "requestId=req_diagnostic1234" in caplog.text
    assert "sk-test-secret" not in caplog.text
    assert "user photo" not in caplog.text
    assert "sk-test-secret" not in str(result)


@pytest.mark.asyncio
async def test_unknown_upstream_values_and_malicious_request_id_are_not_logged(caplog):
    error = UpstreamHTTPError(400)
    error.body = {
        "type": "my-secret-key-1234567",
        "code": "sk-test-untrusted-value",
        "param": "input.private_image_base64",
        "message": "confidential user prompt goes here",
    }
    error.request_id = "req_valid1234?token=secret"
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as raised:
            await invoke(SequenceResponses([error]))
    assert raised.value.upstream_diagnostics == {
        "error_type": None, "error_code": None, "error_param": None,
        "request_id": None, "hint": None,
    }
    for private in ("my-secret-key", "sk-test-untrusted", "private_image",
                    "token=secret", "confidential user prompt"):
        assert private not in caplog.text
        assert private not in str(raised.value)


def test_real_sdk_bad_request_shape_extracts_safe_metadata():
    import httpx
    from libtv_agent.services.ai_creative_planner import classify_planner_upstream_error
    from openai import BadRequestError

    request = httpx.Request("POST", "https://example.invalid/v1/responses")
    response = httpx.Response(
        400, request=request,
        json={"error": {"type": "invalid_request_error",
                        "param": "text.format",
                        "code": "invalid_json_schema",
                        "message": "Unsupported JSON schema; sensitive input"}} ,
        headers={"x-request-id": "req_abcdef987654"},
    )
    error = BadRequestError(
        "Upstream echoed sensitive input", response=response, body=response.json()
    )
    classified = classify_planner_upstream_error(error)
    assert classified.upstream_status == 400
    assert classified.upstream_diagnostics["error_code"] == "invalid_json_schema"
    assert classified.upstream_diagnostics["error_param"] == "text.format"
    assert "sensitive" not in str(classified)

class UpstreamHTTPError(Exception):
    def __init__(self, status_code: int):
        super().__init__("upstream response body must not be shown")
        self.status_code = status_code


class SequenceResponses:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        next_result = self.results.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return SimpleNamespace(output_parsed=next_result)


async def invoke(responses):
    return await plan_creative_video(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput("f1", 1, "data:image/png;base64,AAA")],
        product_images=[],
        reference_role_prompt="References: @image_1 anchors the actor and scene.",
        creative_brief="Show the real garment with lively camera movement",
        brand_tone="", duration_seconds=10, variant_count=1,
        client=SimpleNamespace(responses=responses),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,code", [
    (401, "AI_CREATIVE_AUTH_FAILED"),
    (403, "AI_CREATIVE_AUTH_FAILED"),
    (404, "AI_CREATIVE_ENDPOINT_NOT_FOUND"),
    (400, "AI_CREATIVE_REQUEST_REJECTED"),
    (422, "AI_CREATIVE_REQUEST_REJECTED"),
])
async def test_upstream_error_is_classified_without_retrying_or_exposing_body(status_code, code):
    response = SequenceResponses([UpstreamHTTPError(status_code)])
    with pytest.raises(CreativePlannerError) as error:
        await invoke(response)
    assert error.value.code == code
    assert error.value.upstream_status == status_code
    assert "upstream response body" not in str(error.value)
    assert len(response.calls) == 1


@pytest.mark.asyncio
async def test_legacy_ai_plan_with_unverified_actor_passes_without_local_review():
    good = fixture_solo_plan().variants[0]
    bad_beat = good.beats[0].model_copy(update={"actor_ids": ["invented-person"]})
    returned = good.model_copy(update={"beats": [bad_beat, *good.beats[1:]]})
    response = SequenceResponses([
        CreativeDirectorResponse(observations=fixture_solo_facts(), variants=[returned]),
    ])
    result = await invoke(response)
    assert len(result) == 1
    assert result[0].beats[0]["actor_ids"] == ["invented-person"]
    assert len(response.calls) == 1


@pytest.mark.asyncio
async def test_legacy_invalid_reference_plan_does_not_retry_ai():
    good = fixture_solo_plan().variants[0]
    invalid = good.model_copy(update={"beats": [
        good.beats[0].model_copy(update={"actor_ids": ["invented-person"]}),
        *good.beats[1:],
    ]})
    response = SequenceResponses([
        CreativeDirectorResponse(observations=fixture_solo_facts(), variants=[invalid]),
        UpstreamHTTPError(400),
    ])
    result = await invoke(response)
    assert len(result) == 1
    assert len(response.calls) == 1


@pytest.mark.asyncio
async def test_gateway_error_on_first_call_still_blocks_before_video_submission():
    response = SequenceResponses([UpstreamHTTPError(400)])
    with pytest.raises(CreativePlannerError) as error:
        await invoke(response)
    assert error.value.code == "AI_CREATIVE_REQUEST_REJECTED"
    assert len(response.calls) == 1
