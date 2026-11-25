"""Bounded recovery of opaque Sol relay rejections of inline JPEG references."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from libtv_agent.services.ai_creative_planner import classify_planner_upstream_error
from PIL import Image

logger = logging.getLogger(__name__)
JPEG_PREFIX = "data:image/jpeg;base64,"
PNG_PREFIX = "data:image/png;base64,"
MAX_FALLBACK_BYTES = 20 * 1024 * 1024


def _lossless_png_input(messages: list[dict]) -> list[dict] | None:
    """Preserve text, image order, dimensions, detail and every decoded pixel."""
    converted = []
    changed = False
    total_bytes = 0
    try:
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                converted.append(message)
                continue
            blocks = []
            for block in content:
                url = block.get("image_url", "")
                if block.get("type") == "input_image" and url.startswith(JPEG_PREFIX):
                    raw = base64.b64decode(url[len(JPEG_PREFIX) :], validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format != "JPEG" or image.mode not in {"RGB", "L"}:
                            return None
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                    png = output.getvalue()
                    total_bytes += len(png)
                    if total_bytes > MAX_FALLBACK_BYTES:
                        return None
                    blocks.append(
                        {**block, "image_url": PNG_PREFIX + base64.b64encode(png).decode("ascii")}
                    )
                    changed = True
                else:
                    blocks.append(block)
            converted.append({**message, "content": blocks})
    except (ValueError, OSError, Image.DecompressionBombError):
        return None
    return converted if changed else None


async def _create_response(client, **kwargs):
    """Collect a complete Responses SSE terminal object before returning it.

    Explicitly injected non-SDK clients retain the existing complete-response
    interface used by offline planner tests. Production uses AsyncOpenAI.
    """
    if not isinstance(client, AsyncOpenAI):
        return await client.responses.create(**kwargs)
    started = time.perf_counter()
    first_event = None
    event_count = 0
    outcome = "error"
    stream = await client.responses.create(**kwargs, stream=True)
    try:
        async with stream:
            async for event in stream:
                event_count += 1
                if first_event is None:
                    first_event = time.perf_counter() - started
                if event.type in {"response.failed", "response.incomplete", "response.refusal.delta", "response.refusal.done"}:
                    raise ValueError("Sol stream did not produce a usable completed response")
                if event.type != "response.completed":
                    continue
                response = event.response
                if response.status != "completed" or any(
                    part.type == "refusal"
                    for item in response.output
                    for part in (getattr(item, "content", None) or [])
                ):
                    raise ValueError("Sol stream did not produce a usable completed response")
                outcome = "ok"
                return response
            raise APIConnectionError(message="Sol stream ended before completion", request=stream.response.request)
    except httpx.TimeoutException as exc:
        raise APITimeoutError(request=stream.response.request) from exc
    except httpx.TransportError as exc:
        raise APIConnectionError(message="Sol stream transport interrupted", request=stream.response.request) from exc
    finally:
        logger.info("SOL_STREAM_TIMING outcome=%s events=%d first_event_seconds=%s elapsed_seconds=%.3f",
                    outcome, event_count, round(first_event, 3) if first_event is not None else None,
                    time.perf_counter() - started)


async def create_sol_response(client, **kwargs):
    """Retry the exact known relay 400 once, using lossless image serialization.

    The caller owns the total timeout. Explicit parameter/auth errors and paid
    video requests never enter this path. Original assets are never written.
    """
    try:
        return await _create_response(client, **kwargs)
    except Exception as error:
        failure = classify_planner_upstream_error(error)
        if failure.upstream_status != 400 or failure.code != "AI_CREATIVE_UPSTREAM_UNAVAILABLE":
            raise
        messages = kwargs.get("input")
        if not isinstance(messages, list):
            raise
        converted = await asyncio.to_thread(_lossless_png_input, messages)
        if converted is None:
            raise
        logger.warning("SOL_IMAGE_TRANSPORT_FALLBACK encoding=png attempt=2")
        await asyncio.sleep(1)
        return await _create_response(client, **{**kwargs, "input": converted})
