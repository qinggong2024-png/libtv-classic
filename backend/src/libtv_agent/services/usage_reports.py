"""Read-only usage aggregates; integer microdollars remain the source of truth."""

from libtv_agent.domain.models import AccountUsage
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

CATEGORY_MONEY_FIELDS = {"imageUsedUsd": "IMAGE", "videoUsedUsd": "VIDEO", "cutflowUsedUsd": "CUTFLOW"}
MONEY_FIELDS = ("estimatedUsd", "reservedUsd", "uncertainUsd", "releasedUsd", "budgetUsedUsd", *CATEGORY_MONEY_FIELDS, "otherUsedUsd")


async def aggregate_usage(session: AsyncSession, scope, start, end) -> dict:
    usage = AccountUsage

    def total(condition, value):
        return func.coalesce(func.sum(case((condition, value), else_=0)), 0)

    known_video = (usage.pricing_category.is_not(None)) | (usage.units != 1)
    columns = {
        "recordCount": func.count(usage.id),
        "imageCount": total(usage.kind == "IMAGE", usage.units),
        "videoSeconds": total((usage.kind == "VIDEO") & known_video, usage.units),
        "cutflowClips": total(usage.kind == "CUTFLOW", usage.units),
        "unknownVideoCount": total((usage.kind == "VIDEO") & ~known_video, 1),
        "unpricedCount": total(
            (usage.status != "RELEASED") & usage.amount_microusd.is_(None), 1
        ),
        "budgetUsedUsd": total(usage.status != "RELEASED", usage.amount_microusd),
        **{
            field: total((usage.status != "RELEASED") & (usage.kind == kind), usage.amount_microusd)
            for field, kind in CATEGORY_MONEY_FIELDS.items()
        },
        "otherUsedUsd": total(
            (usage.status != "RELEASED") & ~usage.kind.in_(tuple(CATEGORY_MONEY_FIELDS.values())),
            usage.amount_microusd,
        ),
        **{
            field: total(usage.status == status, usage.amount_microusd)
            for field, status in (
                ("estimatedUsd", "ESTIMATED"),
                ("reservedUsd", "RESERVED"),
                ("uncertainUsd", "UNCERTAIN"),
                ("releasedUsd", "RELEASED"),
            )
        },
    }
    rows = await session.execute(
        select(usage.user_id, *(value.label(key) for key, value in columns.items()))
        .where(
            usage.user_id.in_(scope),
            usage.business_date >= start,
            usage.business_date <= end,
        )
        .group_by(usage.user_id)
    )
    return {
        row.user_id: {key: row._mapping[key] for key in columns}
        for row in rows
    }


def empty_totals() -> dict:
    return dict.fromkeys(
        (*MONEY_FIELDS, "recordCount", "imageCount", "videoSeconds", "cutflowClips",
         "unknownVideoCount", "unpricedCount"), 0
    )


def display_totals(totals: dict) -> dict:
    return {
        key: value / 1_000_000 if key in MONEY_FIELDS else value
        for key, value in totals.items()
    }
