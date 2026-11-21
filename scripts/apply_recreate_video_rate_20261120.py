"""Record the approved ALLWE Classic recreate-video budget rate as a versioned change.

This is an operator-invoked, idempotent runtime-config adjustment, NOT a startup
migration. The default mode is read-only. Only --apply may write the config.
Existing AccountUsage entries and all other prices are never rewritten.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import AccountUsage
from libtv_agent.services.runtime_control import (
    get_runtime_config,
    update_runtime_config,
)
from libtv_agent.services.usage_pricing import PRICING_KEY, global_usage_prices

BEFORE = {
    "imagePriceUsd": 50_000,
    "videoPricePerSecondUsd": 150_000,
    "depthVideoPricePerSecondUsd": 150_000,
    "recreateVideoPricePerSecondUsd": 200_000,
    "cutflowPriceUsd": 150_000,
}
AFTER = {**BEFORE, "recreateVideoPricePerSecondUsd": 300_000}
CHANGE_REASON = (
    "2026-11-20 approved: recreate video estimated USD "
    "$0.20/second -> $0.30/second; other prices unchanged"
)


def proposal(current: dict[str, int | None]) -> dict[str, int | None] | None:
    """Return the only approved pricing change, or None when already applied."""
    if current == AFTER:
        return None
    if current != BEFORE:
        raise ValueError("Pricing baseline differs from approved V40 -> V41 change")
    return AFTER.copy()


async def run(database_path: Path, *, apply: bool) -> None:
    path = database_path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("The database path must point to an existing file")
    settings = Settings(database_url="sqlite+aiosqlite:///" + path.as_posix())
    db = Database(settings.database_url)
    try:
        async with db.session_factory() as session:
            current = await get_runtime_config(session, settings, create=False)
            if current is None:
                raise RuntimeError("Classic runtime configuration is unavailable")
            prices = current.config_json.get(PRICING_KEY)
            if not isinstance(prices, dict):
                raise RuntimeError("Current runtime version has no global pricing map")
            desired = proposal(prices)
            print(f"Runtime version: {current.version}")
            if desired is None:
                print("Verified: recreate video $0.30/second; other prices unchanged.")
                return
            if not apply:
                print("Not yet applied. Would update recreate video $0.20 -> $0.30/second.")
                print("No changes made. Pass --apply to create an audited version.")
                return
            before_count = await session.scalar(select(func.count()).select_from(AccountUsage))
            config = deepcopy(current.config_json)
            config[PRICING_KEY] = desired
            version = await update_runtime_config(
                session,
                settings,
                config=config,
                actor_id=None,
                reason=CHANGE_REASON,
                expected_version=current.version,
            )
            after_count = await session.scalar(select(func.count()).select_from(AccountUsage))
            if before_count != after_count:
                raise RuntimeError("AccountUsage row count changed; refusing to commit")
            if version.config_json[PRICING_KEY] != desired:
                raise RuntimeError("New pricing does not match approved prices")
            await session.commit()
            print(f"Committed audited runtime version {version.version}")
        async with db.session_factory() as session:
            prices, _, number = await global_usage_prices(session)
            if prices != AFTER:
                raise RuntimeError("Committed pricing failed independent verification")
            print(f"Read-back OK: version {number}, recreate $0.30/second")
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Existing Classic SQLite DB")
    parser.add_argument("--apply", action="store_true", help="Write new version and audit event")
    args = parser.parse_args()
    asyncio.run(run(args.database, apply=args.apply))


if __name__ == "__main__":
    main()
