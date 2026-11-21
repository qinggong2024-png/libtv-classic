"""Shared USD estimates; old account rates are read only for a safe initial value."""

from libtv_agent.domain.models import RuntimeConfigVersion, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PRICING_KEY = "globalUsagePricingMicrousd"
PRICE_COLUMNS = {
    "imagePriceUsd": "image_price_microusd",
    "videoPricePerSecondUsd": "video_price_microusd",
    "depthVideoPricePerSecondUsd": "depth_video_price_microusd",
    "recreateVideoPricePerSecondUsd": "recreate_video_price_microusd",
    # 剪辑按「每条原片」计价。该列没有对应的 User 旧列（历史账号从未存过），
    # 因此 fallback 分支必须容忍缺失，见 global_usage_prices。
    "cutflowPriceUsd": "cutflow_price_microusd",
}
CATEGORY_PRICES = dict(
    zip(("IMAGE", "VIDEO", "DEPTH_VIDEO", "RECREATE_VIDEO"), PRICE_COLUMNS, strict=False)
)
CATEGORY_PRICES["CUTFLOW"] = "cutflowPriceUsd"


async def global_usage_prices(session: AsyncSession) -> tuple[dict, list[str], int]:
    current = await session.scalar(
        select(RuntimeConfigVersion).order_by(RuntimeConfigVersion.version.desc()).limit(1)
    )
    version = current.version if current else 0
    if current and PRICING_KEY in current.config_json:
        return current.config_json[PRICING_KEY], [], version
    # No automatic migration or account writes. Only a single agreed legacy rate
    # can become the shared default; conflicting rates require an admin decision.
    prices, conflicts = {}, []
    for key, column in PRICE_COLUMNS.items():
        # 剪辑单价没有历史账号列，缺失时按「未定价」处理，而不是让读取整体失败。
        attribute = getattr(User, column, None)
        if attribute is None:
            prices[key] = None
            continue
        values = set(
            await session.scalars(
                select(attribute).where(attribute.is_not(None)).distinct()
            )
        )
        prices[key] = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1:
            conflicts.append(key)
    return prices, conflicts, version
