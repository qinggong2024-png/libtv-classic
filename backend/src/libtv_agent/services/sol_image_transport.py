"""Bounded recovery of opaque Sol relay rejections of inline JPEG references."""

from __future__ import annotations

import asyncio
import base64
import io
import logging

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


async def create_sol_response(client, **kwargs):
    """Retry the exact known relay 400 once, using lossless image serialization.

    The caller owns the total timeout. Explicit parameter/auth errors and paid
    video requests never enter this path. Original assets are never written.
    """
    try:
        return await client.responses.create(**kwargs)
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
        return await client.responses.create(**{**kwargs, "input": converted})
