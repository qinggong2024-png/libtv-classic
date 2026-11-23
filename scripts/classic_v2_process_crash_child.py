"""Isolated fault-injection child: durably commit a fake pre-paid Sol checkpoint, then die.

Explicit ALLWE_V2_FAULT_INJECTION=LOCAL_ONLY + SQLite temporary db required.
This never calls AI/video providers, a CUT endpoint or production services.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

from libtv_agent.services.ai_creative_planner import CompiledCreativeVariant
from libtv_agent.services.sol_plan_checkpoint import save_plan_checkpoint
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

EXIT_AFTER_CHECKPOINT = 79


async def checkpoint_and_exit(
    database: Path, task_id: uuid.UUID, run_id: uuid.UUID, fingerprint: str,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///" + database.as_posix())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        success = await save_plan_checkpoint(
            session, task_id=task_id, run_id=run_id, fingerprint=fingerprint,
            variants=[CompiledCreativeVariant(
                prompt="Synthetic safe UGC shot with readable shirt print and direct cut",
                creative_idea="A local-only reader design",
                visual_hook="A local-only reveal",
                buyer_reason="Synthetic consumer motive",
                beats=({"action": "look at camera"},),
                facts=({"person_count_verified": True},),
                planner_version="offline-process-fault-injection-v1",
                request_attempts=1,
            )],
        )
        if not success:
            raise RuntimeError("checkpoint rejected by task ownership/CAS")
    # Real OS process termination: no Python shutdown, no ORM teardown.
    # The committed DB transaction must still be visible to the next worker.
    os._exit(EXIT_AFTER_CHECKPOINT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--task-id", type=uuid.UUID, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--fingerprint", required=True)
    args = parser.parse_args()
    if os.environ.get("ALLWE_V2_FAULT_INJECTION") != "LOCAL_ONLY":
        raise SystemExit("fault-injection guard missing")
    db_path = args.database.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root not in db_path.parents or db_path.suffix != ".db":
        raise SystemExit("only temporary synthetic SQLite is permitted")
    if not db_path.is_file():
        raise SystemExit("synthetic DB not found")
    asyncio.run(checkpoint_and_exit(
        db_path, args.task_id, args.run_id, args.fingerprint,
    ))
    return 1  # unreachable after successful deliberate process crash


if __name__ == "__main__":
    sys.exit(main())
