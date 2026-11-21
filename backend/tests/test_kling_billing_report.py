"""Supplier billing is display-only and never an ALLWE points conversion."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from libtv_agent.domain.enums import AttemptStatus, UserRole, VariantKind
from libtv_agent.domain.models import Base, GenerationAttempt, GenerationVariant, ParentTask, User
from libtv_agent.services.kling_billing_report import kling_billing_report, summarize_kling_billing
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _record(billing, *, title="Example Task"):
    attempt = SimpleNamespace(
        id=uuid4(), task_id="provider-123", provider_model="kling-4.0",
        provider="kling_official_api", credits_consumed=None,
        provider_status="succeeded", created_at=datetime(2026, 10, 30, tzinfo=UTC),
        raw_response_json={"billing": billing, "outputUrl": "private-not-exposed"},
    )
    task = SimpleNamespace(id=uuid4(), title=title)
    return attempt, task


def test_kling_decimal_receipts_are_exact_and_separate_from_internal_points():
    record = _record([{"charge_type": "unit", "amount": "22.5", "package_type": "video"}])
    extra = _record([{"charge_type": "unit", "amount": "1.25", "package_type": "video"}])
    cash = _record([{"charge_type": "cash", "amount": "3.10"}])
    report = summarize_kling_billing([record, extra, cash], scanned_limit=100)
    assert report["totals"] == [
        {"type": "cash", "packageType": None, "amount": "3.10"},
        {"type": "unit", "packageType": "video", "amount": "23.75"},
    ]
    assert report["items"][0]["charges"][0]["amount"] == "22.5"
    assert report["receiptCount"] == 3
    assert "private-not-exposed" not in str(report)
    assert "internalPoints" not in str(report)
    assert "chargeStatus" not in str(report)


def test_missing_and_invalid_provider_billing_are_not_assumed_free_or_charged():
    missing = _record(None)
    malformed = _record([{"charge_type": "unit", "amount": "not-a-number"}])
    report = summarize_kling_billing([missing, malformed], scanned_limit=100)
    assert report["scannedAttempts"] == 2
    assert report["receiptCount"] == 0
    assert report["invalidReceiptCount"] == 1
    assert report["totals"] == []


def test_cli_credits_are_visible_without_fabricating_api_cash_or_internal_points():
    cli = _record(None, title="CLI Task")
    cli[0].provider = "kling_official_cli"
    cli[0].provider_model = "kling-video-v3_0_omni"
    cli[0].credits_consumed = 168
    uncertain = _record(None, title="Not billed yet")
    uncertain[0].provider = "kling_official_cli"
    report = summarize_kling_billing([cli, uncertain], scanned_limit=100)
    assert report["source"] == "stored_kling_provider_task_receipts"
    assert report["scannedAttempts"] == 2
    assert report["receiptCount"] == 1
    assert report["cliCreditsTotal"] == 168
    assert report["cliCreditReceiptCount"] == 1
    assert report["totals"] == []
    assert report["items"][0]["creditsConsumed"] == 168
    assert report["items"][0]["provider"] == "kling_official_cli"
    assert "internalPoints" not in str(report)

@pytest.mark.asyncio
async def test_read_only_billing_query_joins_to_tasks_without_settling_points(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + (tmp_path / "receipts.db").as_posix()
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner = User(
                username="billing-report-owner", password_hash="disabled",
                role=UserRole.USER,
            )
            session.add(owner)
            await session.flush()
            task = ParentTask(
                user_id=owner.id, client_task_id="billing-ex", title="Source Task", asset_count=0,
            )
            session.add(task)
            await session.flush()
            variant = GenerationVariant(
                parent_task_id=task.id, variant_type=VariantKind.STABLE, revision=1,
            )
            session.add(variant)
            await session.flush()
            attempt = GenerationAttempt(
                variant_id=variant.id, provider="kling_official_api",
                provider_model="kling-4.0", task_id="upstream-123",
                status=AttemptStatus.SUCCEEDED, provider_status="succeeded",
                raw_response_json={
                    "billing": [{"charge_type": "unit", "amount": "22.5", "package_type": "video"}],
                    "videoUrl": "https://private.example/video.mp4",
                },
            )
            session.add(attempt)
            await session.commit()
        async with factory() as session:
            before = (await session.get(GenerationAttempt, attempt.id)).charge_status
            report = await kling_billing_report(session)
            after = (await session.get(GenerationAttempt, attempt.id)).charge_status
            assert before == after
            assert report["receiptCount"] == 1
            assert report["items"][0]["providerTaskId"] == "upstream-123"
            assert report["totals"] == [{
                "type": "unit", "packageType": "video", "amount": "22.5",
            }]
            assert "private.example" not in str(report)
            cli_variant = GenerationVariant(
                parent_task_id=task.id, variant_type=VariantKind.STABLE, revision=2,
            )
            session.add(cli_variant)
            await session.flush()
            cli = GenerationAttempt(
                variant_id=cli_variant.id, provider="kling_official_cli",
                provider_model="kling-video-v3_0_omni", task_id="cli-remote-123",
                status=AttemptStatus.SUCCEEDED, provider_status="succeeded",
                credits_consumed=168, raw_response_json={"generationId": "cli-remote-123"},
            )
            session.add(cli)
            await session.commit()
            current = await kling_billing_report(session)
            assert current["cliCreditsTotal"] == 168
            assert current["cliCreditReceiptCount"] == 1
            assert current["receiptCount"] == 2
    finally:
        await engine.dispose()
