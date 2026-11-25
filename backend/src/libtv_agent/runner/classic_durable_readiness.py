"""Heartbeat is emitted only by the compatible running singleton Runner."""

from __future__ import annotations

import asyncio
import logging
import time

from libtv_agent.runner.classic_checkpoint import atomic_json
from libtv_agent.services.classic_durable_config import (
    READY,
    effective_runner_revision,
    matches_loaded,
    read_config,
)
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def publish_readiness(database, settings, stopping, workers):
    path = settings.classic_durable_config_file
    receipt_path = path.parent / READY

    def publish(ready):
        atomic_json(
            receipt_path,
            {
                **stopping.identity,
                "configRevision": settings.classic_durable_config_revision,
                "analysis": settings.classic_durable_analysis_enabled,
                "confirmation": settings.classic_durable_confirmation_enabled,
                "analysisConcurrency": settings.classic_durable_analysis_concurrency,
                "heartbeat": time.time(),
                "ready": ready,
            },
        )

    try:
        while not stopping.is_set():
            ready = False
            try:
                data = read_config(path)
                expected_workers = (
                    settings.classic_durable_analysis_concurrency + 1
                    if settings.classic_durable_analysis_enabled
                    else 0
                ) + int(settings.classic_durable_confirmation_enabled)
                if (
                    matches_loaded(settings, data)
                    and effective_runner_revision(path.parent, data)
                    == stopping.identity["sourceRevision"]
                    and len(workers) == expected_workers
                    and all(not worker.done() for worker in workers)
                ):
                    async with database.session_factory() as session:
                        # Check actual schema access, not merely process existence.
                        await session.execute(
                            text("SELECT analysis_run_id, analysis_stage FROM parent_task LIMIT 0")
                        )
                        await session.execute(text("SELECT event_type FROM outbox_event LIMIT 0"))
                    ready = not stopping.is_set()
            except Exception:
                logger.exception("durable Runner readiness check failed")
            try:
                publish(ready)
            except OSError as exc:
                # A concurrent Windows read can briefly deny atomic replacement.
                # Do not let that transient error permanently kill the publisher.
                logger.warning("Runner readiness write failed: %s", type(exc).__name__)
            await asyncio.sleep(2)
    finally:
        try:
            publish(False)
        except OSError as exc:
            logger.warning("Runner readiness final write failed: %s", type(exc).__name__)
