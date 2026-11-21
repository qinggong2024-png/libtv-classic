"""Read-only Kling API and CLI receipts, separate from ALLWE account budgets."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC
from decimal import Decimal
from typing import Any

from libtv_agent.adapters.kling_official_api import KlingApiError, _parse_billing
from libtv_agent.domain.models import GenerationAttempt, GenerationVariant, ParentTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def summarize_kling_billing(
    records: list[tuple[GenerationAttempt, ParentTask]],
    *,
    scanned_limit: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals: dict[tuple[str, str | None], Decimal] = defaultdict(lambda: Decimal("0"))
    invalid_count = 0
    cli_credits_total = 0
    cli_credit_receipt_count = 0
    for attempt, task in records:
        response = attempt.raw_response_json
        charges = ()
        if isinstance(response, dict) and response.get("billing"):
            try:
                charges = _parse_billing(response)
            except (KlingApiError, KeyError, TypeError, ValueError):
                invalid_count += 1
                continue
        cli_credits = (
            attempt.credits_consumed
            if attempt.provider == "kling_official_cli"
            and isinstance(attempt.credits_consumed, int)
            and not isinstance(attempt.credits_consumed, bool)
            and attempt.credits_consumed >= 0
            else None
        )
        if not charges and cli_credits is None:
            continue
        if cli_credits is not None:
            cli_credits_total += cli_credits
            cli_credit_receipt_count += 1
        charge_rows = []
        for charge in charges:
            totals[(charge.charge_type, charge.package_type)] += charge.amount
            charge_rows.append({
                "type": charge.charge_type,
                "amount": str(charge.amount),
                "packageType": charge.package_type,
                "listPrice": str(charge.list_price) if charge.list_price is not None else None,
            })
        rows.append({
            "attemptId": str(attempt.id),
            "taskId": str(task.id),
            "taskTitle": task.title,
            "providerTaskId": attempt.task_id,
            "provider": attempt.provider,
            "model": attempt.provider_model,
            "status": attempt.provider_status or attempt.status.value,
            "creditsConsumed": cli_credits,
            "createdAt": (
                attempt.created_at.replace(tzinfo=UTC)
                if attempt.created_at.tzinfo is None else attempt.created_at
            ).isoformat(),
            "charges": charge_rows,
        })
    return {
        "source": "stored_kling_provider_task_receipts",
        "scope": "most_recent_provider_attempts",
        "scanLimit": scanned_limit,
        "scannedAttempts": len(records),
        "receiptCount": len(rows),
        "invalidReceiptCount": invalid_count,
        "cliCreditsTotal": cli_credits_total,
        "cliCreditReceiptCount": cli_credit_receipt_count,
        "totals": [
            {
                "type": kind,
                "packageType": package,
                "amount": str(value),
            }
            for (kind, package), value in sorted(
                totals.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ],
        "items": rows,
    }


async def kling_billing_report(
    session: AsyncSession, *, limit: int = 100
) -> dict[str, Any]:
    records = list((await session.execute(
        select(GenerationAttempt, ParentTask)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(GenerationAttempt.provider.in_(
            ("kling_official_api", "kling_official_cli")
        ))
        .order_by(GenerationAttempt.created_at.desc(), GenerationAttempt.id.desc())
        .limit(limit)
    )).all())
    return summarize_kling_billing(records, scanned_limit=limit)
