"""AI director transport retries are bounded and never touch video submission."""

from types import SimpleNamespace

import pytest
from libtv_agent.services.ai_creative_planner import CreativeDirectorResponse, CreativePlannerError
from test_ai_creative_error_handling import SequenceResponses, UpstreamHTTPError, invoke
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


def valid_response():
    return CreativeDirectorResponse(
        observations=fixture_solo_facts(), variants=fixture_solo_plan().variants
    )


@pytest.fixture
def delays(monkeypatch):
    values = []

    async def fake_sleep(seconds):
        values.append(seconds)

    monkeypatch.setattr("libtv_agent.services.ai_creative_planner.asyncio.sleep", fake_sleep)
    return values


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
async def test_transient_failure_retries_once_with_identical_request(
    status_code, delays, caplog,
):
    response = SequenceResponses([UpstreamHTTPError(status_code), valid_response()])
    with caplog.at_level("INFO"):
        compiled = await invoke(response)
    assert len(compiled) == 1
    assert compiled[0].request_attempts == 2
    assert len(response.calls) == 2
    assert response.calls[0]["input"] == response.calls[1]["input"]
    assert delays == [5.0 if status_code == 429 else 3.0]
    assert "attempt=2/2" in caplog.text
    assert "completed after a transient retry" in caplog.text


@pytest.mark.asyncio
async def test_transient_failure_twice_exhausts_retry_budget(delays, caplog):
    response = SequenceResponses([UpstreamHTTPError(502), UpstreamHTTPError(503)])
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as raised:
            await invoke(response)
    assert len(response.calls) == 2
    assert delays == [3.0]
    assert raised.value.upstream_status == 503
    assert raised.value.request_attempts == 2
    assert "requestAttempts=2" in caplog.text
    assert "upstream response body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retry_after,expected_delay,expected_count",
    [("7", 7.0, 2), ("0", 1.0, 2), ("30", None, 1), ("bad", None, 1)],
)
async def test_rate_limit_respects_retry_after_cap(
    retry_after, expected_delay, expected_count, delays,
):
    error = UpstreamHTTPError(429)
    error.response = SimpleNamespace(headers={"retry-after": retry_after})
    response = SequenceResponses([error, valid_response()])
    if expected_count == 1:
        with pytest.raises(CreativePlannerError):
            await invoke(response)
        assert delays == []
    else:
        await invoke(response)
        assert delays == [expected_delay]
    assert len(response.calls) == expected_count


@pytest.mark.asyncio
async def test_timeout_recovers_with_one_retry(delays):
    response = SequenceResponses([
        TimeoutError("private upstream text"),
        valid_response(),
    ])
    await invoke(response)
    assert len(response.calls) == 2
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_retry_budget_is_shared_with_structure_correction(delays):
    good = fixture_solo_plan().variants[0]
    invalid = good.model_copy(update={
        "beats": [
            good.beats[0].model_copy(update={"actor_ids": ["unknown-actor"]}),
            *good.beats[1:],
        ]
    })
    response = SequenceResponses([
        UpstreamHTTPError(503),
        CreativeDirectorResponse(observations=fixture_solo_facts(), variants=[invalid]),
        UpstreamHTTPError(502),
    ])
    with pytest.raises(CreativePlannerError) as raised:
        await invoke(response)
    assert raised.value.request_attempts == 3
    assert raised.value.upstream_status == 502
    assert delays == [3.0]
    assert len(response.calls) == 3  # One transport retry, one existing plan correction.


@pytest.mark.asyncio
async def test_permanent_errors_do_not_retry_even_if_500_happened_before(delays):
    response = SequenceResponses([UpstreamHTTPError(400), valid_response()])
    with pytest.raises(CreativePlannerError) as raised:
        await invoke(response)
    assert raised.value.request_attempts == 1
    assert len(response.calls) == 1
    assert delays == []


@pytest.mark.asyncio
async def test_two_transient_calls_share_one_total_deadline(monkeypatch, delays):
    from libtv_agent.services import ai_creative_planner

    timeouts = []
    original = ai_creative_planner._parse_provider

    async def recording_provider(*args, **kwargs):
        timeouts.append(kwargs["request_timeout_seconds"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(ai_creative_planner, "_parse_provider", recording_provider)
    response = SequenceResponses([UpstreamHTTPError(504), valid_response()])
    await invoke(response)
    assert len(timeouts) == 2
    assert 125.0 <= timeouts[0] <= 135.0
    # Subtracting monotonic timestamps can round 210s upward by a few picoseconds.
    assert 0.0 < timeouts[1] <= 210.0 + 1e-6
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_global_budget_aborts_before_unbounded_request(monkeypatch):
    from libtv_agent.services import ai_creative_planner

    monkeypatch.setattr(ai_creative_planner, "PLANNER_TOTAL_WAIT_SECONDS", 5.0)
    response = SequenceResponses([valid_response()])
    with pytest.raises(CreativePlannerError) as raised:
        await invoke(response)
    assert raised.value.code == "AI_CREATIVE_REQUEST_TIMEOUT"
    assert raised.value.request_attempts == 0
    assert response.calls == []


@pytest.mark.asyncio
async def test_correction_requests_consume_same_budget(monkeypatch, delays):
    from libtv_agent.services import ai_creative_planner

    durations = []
    actual = ai_creative_planner._parse_provider

    async def recording_provider(*args, **kwargs):
        durations.append(kwargs["request_timeout_seconds"])
        return await actual(*args, **kwargs)

    monkeypatch.setattr(ai_creative_planner, "_parse_provider", recording_provider)
    good = fixture_solo_plan().variants[0]
    invalid = good.model_copy(update={
        "beats": [
            good.beats[0].model_copy(update={"actor_ids": ["unknown-person"]}),
            *good.beats[1:],
        ]
    })
    response = SequenceResponses([
        CreativeDirectorResponse(observations=fixture_solo_facts(), variants=[invalid]),
        valid_response(),
    ])
    final = await invoke(response)
    assert len(final) == 1
    assert final[0].request_attempts == 2
    assert len(durations) == 2
    assert 125.0 <= durations[0] <= 135.0
    assert durations[1] <= 210.0 + 1e-6
    assert delays == []
