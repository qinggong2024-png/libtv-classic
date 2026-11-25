import base64
import copy
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import Request, Response
from libtv_agent.services.sol_image_transport import create_sol_response
from openai import BadRequestError
from PIL import Image


def rejection(*, code="invalid_request", param=None):
    response = Response(400, request=Request("POST", "https://example.invalid/v1/responses"))
    return BadRequestError(
        "rejected",
        response=response,
        body={
            "error": {
                "type": "invalid_request_error",
                "code": code,
                "param": param,
                "message": (
                    "The request could not be processed. Please check the request parameters."
                ),
            }
        },
    )


def request():
    output = io.BytesIO()
    Image.new("RGB", (24, 32), (10, 90, 170)).save(output, format="JPEG")
    url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
    return {
        "model": "gpt-6.2-sol",
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 2200,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "Original instructions."}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Original brief and image roles."},
                    {"type": "input_image", "image_url": url, "detail": "high"},
                    {"type": "input_image", "image_url": url, "detail": "low"},
                ],
            },
        ],
    }


@pytest.mark.asyncio
async def test_opaque_relay_fallback_preserves_instructions_and_pixels(monkeypatch):
    monkeypatch.setattr("libtv_agent.services.sol_image_transport.asyncio.sleep", AsyncMock())
    response = SimpleNamespace(output_text="original response")
    responses = SimpleNamespace(create=AsyncMock(side_effect=[rejection(), response]))
    original = request()
    saved = copy.deepcopy(original)
    assert await create_sol_response(SimpleNamespace(responses=responses), **original) is response
    first, second = [call.kwargs for call in responses.create.call_args_list]
    assert first == saved == original
    restored = copy.deepcopy(second)
    for old, new, restored_block in zip(
        first["input"][1]["content"],
        second["input"][1]["content"],
        restored["input"][1]["content"],
        strict=True,
    ):
        if old["type"] == "input_image":
            assert new["image_url"].startswith("data:image/png;base64,")
            with Image.open(io.BytesIO(base64.b64decode(old["image_url"].split(",")[1]))) as jpeg:
                with Image.open(
                    io.BytesIO(base64.b64decode(new["image_url"].split(",")[1]))
                ) as png:
                    assert (jpeg.size, jpeg.mode, jpeg.tobytes()) == (
                        png.size,
                        png.mode,
                        png.tobytes(),
                    )
            restored_block["image_url"] = old["image_url"]
    assert restored == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,param", [("invalid_json_schema", None), ("invalid_request", "model")]
)
async def test_explicit_parameter_rejection_is_not_retried(code, param):
    error = rejection(code=code, param=param)
    responses = SimpleNamespace(create=AsyncMock(side_effect=error))
    with pytest.raises(BadRequestError) as caught:
        await create_sol_response(SimpleNamespace(responses=responses), **request())
    assert caught.value is error
    assert responses.create.await_count == 1


@pytest.mark.asyncio
async def test_opaque_relay_fallback_is_bounded(monkeypatch):
    monkeypatch.setattr("libtv_agent.services.sol_image_transport.asyncio.sleep", AsyncMock())
    responses = SimpleNamespace(create=AsyncMock(side_effect=rejection()))
    with pytest.raises(BadRequestError):
        await create_sol_response(SimpleNamespace(responses=responses), **request())
    assert responses.create.await_count == 2


@pytest.mark.asyncio
async def test_text_only_and_invalid_image_rejections_preserve_original_error():
    for content in [
        [{"type": "input_text", "text": "review"}],
        [{"type": "input_image", "image_url": "data:image/jpeg;base64,broken"}],
    ]:
        error = rejection()
        responses = SimpleNamespace(create=AsyncMock(side_effect=error))
        with pytest.raises(BadRequestError) as caught:
            await create_sol_response(
                SimpleNamespace(responses=responses), input=[{"role": "user", "content": content}]
            )
        assert caught.value is error
        assert responses.create.await_count == 1


# Exercise the actual OpenAI SDK SSE decoder, without any external HTTP.
import json
import asyncio
import httpx
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError


def _sse(event_type, **fields):
    return ("event: " + event_type + "\ndata: " + json.dumps({"type": event_type, "sequence_number": 1, **fields}) + "\n\n").encode()


def _terminal(status="completed", refusal=False):
    part = {"type": "refusal", "refusal": "not available"} if refusal else {
        "type": "output_text", "text": "Complete director text. CUT TO next shot.", "annotations": []}
    return {"id": "resp_offline", "object": "response", "created_at": 1,
            "model": "gpt-6.2-sol", "status": status,
            "output": [{"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed", "content": [part]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}


@pytest.mark.asyncio
async def test_sdk_stream_preserves_payload_and_returns_terminal_response(caplog):
    original = request()
    calls = []
    async def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=
            _sse("response.output_text.delta", delta="partial", item_id="msg_offline", output_index=0, content_index=0)
            + _sse("response.completed", response=_terminal()))
    async with AsyncOpenAI(api_key="offline", base_url="https://example.invalid/v1", max_retries=0,
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        result = await create_sol_response(client, **original)
    assert calls == [{**original, "stream": True}]
    assert result.status == "completed"
    assert result.output_text == "Complete director text. CUT TO next shot."


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["response.failed", "response.incomplete", "response.refusal.delta", "refusal_terminal", "eof"])
async def test_sdk_stream_rejects_partial_or_refused_output(event):
    calls = []
    async def handler(req):
        calls.append(json.loads(req.content))
        partial = _sse("response.output_text.delta", delta="partial", item_id="msg_offline", output_index=0, content_index=0)
        if event == "eof": terminal = b""
        elif event == "refusal_terminal": terminal = _sse("response.completed", response=_terminal(refusal=True))
        elif event == "response.refusal.delta": terminal = _sse(event, delta="refused", item_id="msg_offline", output_index=0, content_index=0)
        else: terminal = _sse(event, response=_terminal(status=event.split(".")[1]))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=partial+terminal)
    async with AsyncOpenAI(api_key="offline", base_url="https://example.invalid/v1", max_retries=0,
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        with pytest.raises(APIConnectionError if event == "eof" else ValueError):
            await create_sol_response(client, **request())
    assert len(calls) == 1


class _BrokenStream(httpx.AsyncByteStream):
    def __init__(self, mode):
        self.mode = mode
        self.closed = False
    async def __aiter__(self):
        yield _sse("response.output_text.delta", delta="partial", item_id="msg_offline", output_index=0, content_index=0)
        if self.mode == "timeout": raise httpx.ReadTimeout("offline")
        if self.mode == "disconnect": raise httpx.ReadError("offline")
        await asyncio.sleep(10)
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "disconnect", "cancel"])
async def test_sdk_stream_closes_on_timeout_disconnect_or_cancellation(mode):
    body = _BrokenStream(mode)
    async def handler(req):
        return httpx.Response(200, headers={"Content-Type":"text/event-stream"}, stream=body)
    async with AsyncOpenAI(api_key="offline", base_url="https://example.invalid/v1", max_retries=0,
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        expected = {"timeout":APITimeoutError,"disconnect":APIConnectionError,"cancel":TimeoutError}[mode]
        with pytest.raises(expected):
            await asyncio.wait_for(create_sol_response(client, **request()), timeout=0.1 if mode=="cancel" else 5)
        assert body.closed
