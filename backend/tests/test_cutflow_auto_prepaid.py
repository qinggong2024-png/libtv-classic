"""CUT budget admission, once-only transfer and safe release.

Runs entirely against the isolated SQLite fixture: no provider, NAS or CUT calls.
"""
import uuid
from types import SimpleNamespace

import pytest
import test_cutflow_auto_submit_v2 as base
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.enums import (
    DownloadStatus,
    GenerationStatus,
    ParentTaskPhase,
    ValidationStatus,
    VariantKind,
)
from libtv_agent.domain.models import (
    AccountUsage,
    GenerationAttempt,
    GenerationCommand,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
    RuntimeConfigVersion,
    User,
)
from libtv_agent.services import fixed_video_tasks
from libtv_agent.services.account_access import reserve_usd
from libtv_agent.services.cutflow_auto_submit_v2 import enqueue_completed_v2
from libtv_agent.services.cutflow_budget import (
    BUDGET_KEY,
    release_unused_auto_cutflow,
    reserve_auto_cutflow,
)
from sqlalchemy import select

base_handoff_env = base.base_handoff_env
handoff_env = base.handoff_env


async def _prepare(env, *, budget=20_000_000):
    await base.prepare(env)
    database, _, _, ids = env
    async with database.session_factory() as s:
        u = await s.get(User, ids["user"])
        u.daily_budget_microusd = budget
        await s.commit()


async def test_prepaid_auto_edit_survives_exhausted_remaining_budget_without_double_charge(
    handoff_env,
):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=150_000)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        attempt = uuid.uuid4()
        await reserve_auto_cutflow(s, parent=parent, attempt_id=attempt, units=1)
        await reserve_auto_cutflow(s, parent=parent, attempt_id=attempt, units=1)
        held_id = parent.settings_json[BUDGET_KEY]
        await s.commit()
    async with db.session_factory() as s:
        user = await s.get(User, ids["user"])
        user.daily_budget_microusd = 1
        await s.commit()

    assert await enqueue_completed_v2(db, settings, store) == 1
    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        jobs = (await s.scalars(select(MultiCutflowTask))).all()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(jobs) == len(usage) == 1
        assert usage[0].operation_id == jobs[0].id
        assert usage[0].amount_microusd == 150_000
        assert usage[0].status == "RESERVED"
        assert str(jobs[0].id) != held_id


async def test_two_clip_prepaid_budget_shrinks_when_only_one_clip_survives(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=300_000)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        parent.phase = ParentTaskPhase.PARTIALLY_DELIVERABLE
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=2)
        held_id = parent.settings_json[BUDGET_KEY]
        s.add(GenerationVariant(
            id=uuid.uuid4(), parent_task_id=ids["task"],
            variant_type=VariantKind.DYNAMIC, variant_index=2,
            generation_status=GenerationStatus.FAILED,
            download_status=DownloadStatus.NOT_READY,
            validation_status=ValidationStatus.FAILED,
        ))
        await s.commit()
    assert await enqueue_completed_v2(db, settings, store) == 1
    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        jobs = (await s.scalars(select(MultiCutflowTask))).all()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(jobs) == len(usage) == 1
        assert str(usage[0].operation_id) != held_id
        assert usage[0].operation_id == jobs[0].id
        assert usage[0].units == 1
        assert usage[0].amount_microusd == 150_000


async def test_manual_partial_takeover_uses_same_auto_identity_and_prepayment(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=300_000)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        parent.phase = ParentTaskPhase.PARTIALLY_DELIVERABLE
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=2)
        s.add(GenerationVariant(
            id=uuid.uuid4(), parent_task_id=ids["task"],
            variant_type=VariantKind.DYNAMIC, variant_index=2,
            generation_status=GenerationStatus.FAILED,
            download_status=DownloadStatus.NOT_READY,
            validation_status=ValidationStatus.FAILED,
        ))
        await s.commit()
    app = create_app(settings)
    app.state.database = db
    body = {
        "sourceIds": [str(ids["variant"])],
        "idempotencyKey": f"auto-unified-v2:{ids['task']}",
        "confirmPolicy": True,
        "allowSingleSource": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/cutflow-handoffs/multi", json=body,
                                  headers={"X-User-Id": str(ids["user"])})
        assert first.status_code == 202, first.text
        repeat = await client.post("/api/v1/cutflow-handoffs/multi", json=body,
                                   headers={"X-User-Id": str(ids["user"])})
        assert repeat.status_code == 202, repeat.text
        assert first.json()["id"] == repeat.json()["id"]

    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        jobs = (await s.scalars(select(MultiCutflowTask))).all()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(jobs) == len(usage) == 1
        assert usage[0].operation_id == jobs[0].id
        assert usage[0].units == 1 and usage[0].amount_microusd == 150_000


async def test_prepaid_and_video_same_transaction_roll_back_on_budget_failure(handoff_env):
    db, _, _, ids = handoff_env
    await _prepare(handoff_env, budget=300_000)
    async with db.session_factory() as s:
        s.add(RuntimeConfigVersion(
            version=2,
            config_json={"globalUsagePricingMicrousd": {
                "cutflowPriceUsd": 150_000,
                "videoPricePerSecondUsd": 250_000,
            }},
            change_reason="combined-admission test",
        ))
        await s.commit()
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        with pytest.raises(HTTPException) as error:
            await reserve_usd(
                s, user_id=ids["user"], kind="VIDEO",
                operation_id=uuid.uuid4(), units=1,
            )
        assert error.value.detail["code"] == "USD_DAILY_BUDGET_EXCEEDED"
        await s.rollback()
    async with db.session_factory() as s:
        assert (await s.scalars(select(AccountUsage))).all() == []
        parent = await s.get(ParentTask, ids["task"])
        assert BUDGET_KEY not in parent.settings_json


async def test_prepaid_released_after_terminal_generation_failure(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        parent.phase = ParentTaskPhase.ACTION_REQUIRED
        variant = await s.get(GenerationVariant, ids["variant"])
        variant.generation_status = GenerationStatus.FAILED
        await s.commit()
    assert await enqueue_completed_v2(db, settings, store) == 1
    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(usage) == 1 and usage[0].status == "RELEASED"


async def test_release_does_not_touch_funds_already_transferred_to_cut(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        await s.commit()
    assert await enqueue_completed_v2(db, settings, store) == 1
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        assert await release_unused_auto_cutflow(s, parent) is False
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).one()
        assert usage.status == "RESERVED"




async def test_queue_video_task_requires_both_budgets_before_any_provider_command(
    handoff_env, monkeypatch,
):
    """Real queue admission: CUT hold + video charge must be all-or-nothing."""
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=300_000)
    async with db.session_factory() as s:
        s.add(RuntimeConfigVersion(
            version=2,
            config_json={"globalUsagePricingMicrousd": {
                "cutflowPriceUsd": 150_000,
                "videoPricePerSecondUsd": 250_000,
            }},
            change_reason="queue admission test",
        ))
        parent = await s.get(ParentTask, ids["task"])
        parent.workflow_kind = "FIXED_TEMPLATE"
        parent.phase = ParentTaskPhase.PREPARING
        await s.commit()

    async def noop(*_args, **_kwargs):
        return None

    async def fake_prepare(session, *_args, **_kwargs):
        return [await session.get(GenerationAttempt, ids["attempt"])]

    monkeypatch.setattr(fixed_video_tasks, "ensure_provider_submission_allowed", noop)
    monkeypatch.setattr(fixed_video_tasks, "prepare_kling_task", fake_prepare)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        result = await fixed_video_tasks.queue_video_task(
            s, task=parent, user_id=ids["user"], settings=settings,
            kling_adapter=SimpleNamespace(get_provider_name=lambda: "kling"),
            object_store=store,
        )
        assert result.phase == ParentTaskPhase.ACTION_REQUIRED
        assert result.phase_reason_code == "USD_DAILY_BUDGET_EXCEEDED"
    async with db.session_factory() as s:
        assert (await s.scalars(select(AccountUsage))).all() == []
        assert (await s.scalars(select(GenerationCommand))).all() == []
        parent = await s.get(ParentTask, ids["task"])
        assert BUDGET_KEY not in parent.settings_json


async def test_cut_worker_offline_does_not_block_original_video_admission(
    handoff_env, monkeypatch,
):
    """Reserve CUT once, queue original now, dispatch the edit after CUT recovers."""
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=2_000_000)
    settings.cutflow_multi_source_enabled = False
    async with db.session_factory() as s:
        s.add(RuntimeConfigVersion(
            version=2,
            config_json={"globalUsagePricingMicrousd": {
                "cutflowPriceUsd": 150_000,
                "videoPricePerSecondUsd": 100_000,
            }},
            change_reason="offline CUT video-admission test",
        ))
        parent = await s.get(ParentTask, ids["task"])
        parent.workflow_kind = "FIXED_TEMPLATE"
        parent.phase = ParentTaskPhase.PREPARING
        await s.commit()

    async def noop(*_args, **_kwargs):
        return None

    async def fake_prepare(session, *_args, **_kwargs):
        return [await session.get(GenerationAttempt, ids["attempt"])]

    monkeypatch.setattr(fixed_video_tasks, "ensure_provider_submission_allowed", noop)
    monkeypatch.setattr(fixed_video_tasks, "prepare_kling_task", fake_prepare)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        result = await fixed_video_tasks.queue_video_task(
            s, task=parent, user_id=ids["user"], settings=settings,
            kling_adapter=SimpleNamespace(get_provider_name=lambda: "kling"),
            object_store=store,
        )
        assert result.phase == ParentTaskPhase.QUEUED
    async with db.session_factory() as s:
        held = (await s.scalars(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
        ))).one()
        assert held.status == "RESERVED"
        assert (await s.scalars(select(GenerationCommand))).all()
    assert await enqueue_completed_v2(db, settings, store) == 0
    # The normal worker is permitted to resume when CUT comes back online.
    settings.cutflow_multi_source_enabled = True
    assert await enqueue_completed_v2(db, settings, store) == 1


@pytest.mark.parametrize(
    "cut_price,budget",
    [(None, 2_000_000), (3_000_000, 2_000_000), (150_000, 1_000_000)],
)
async def test_cut_prepayment_problem_stays_edit_only_while_video_budget_is_safe(
    handoff_env, monkeypatch, cut_price, budget,
):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env, budget=budget)
    async with db.session_factory() as s:
        s.add(RuntimeConfigVersion(
            version=2,
            config_json={"globalUsagePricingMicrousd": {
                "cutflowPriceUsd": cut_price,
                "videoPricePerSecondUsd": 100_000,
            }},
            change_reason="CUT insufficient funds must not block original",
        ))
        parent = await s.get(ParentTask, ids["task"])
        parent.workflow_kind = "FIXED_TEMPLATE"
        parent.phase = ParentTaskPhase.PREPARING
        await s.commit()

    async def noop(*_args, **_kwargs):
        return None

    async def fake_prepare(session, *_args, **_kwargs):
        return [await session.get(GenerationAttempt, ids["attempt"])]

    monkeypatch.setattr(fixed_video_tasks, "ensure_provider_submission_allowed", noop)
    monkeypatch.setattr(fixed_video_tasks, "prepare_kling_task", fake_prepare)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        queued = await fixed_video_tasks.queue_video_task(
            s, task=parent, user_id=ids["user"], settings=settings,
            kling_adapter=SimpleNamespace(get_provider_name=lambda: "kling"),
            object_store=store,
        )
        assert queued.phase == ParentTaskPhase.QUEUED
        assert queued.settings_json["autoCutflowPrepayWarning"] in {
            "USD_PRICE_REQUIRED", "USD_DAILY_BUDGET_EXCEEDED",
        }
    async with db.session_factory() as s:
        usage = (await s.scalars(select(AccountUsage))).all()
        assert len(usage) == 1
        assert usage[0].kind == "VIDEO" and usage[0].amount_microusd == 1_000_000
        assert (await s.scalars(select(GenerationCommand))).all()




async def test_incomplete_video_keeps_cut_budget_held_until_source_is_ready(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        variant = await s.get(GenerationVariant, ids["variant"])
        variant.generation_status = GenerationStatus.RUNNING
        await s.commit()
    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        held = (await s.scalars(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
        ))).one()
        assert held.status == "RESERVED"
        assert (await s.scalars(select(MultiCutflowTask))).all() == []


async def test_existing_manual_edit_releases_unused_prepaid_auto_hold(handoff_env):
    db, settings, store, ids = handoff_env
    await _prepare(handoff_env)
    manual_id = uuid.uuid4()
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        s.add(MultiCutflowTask(
            id=manual_id, user_id=ids["user"], idempotency_key="manual-existing",
            payload_hash="a" * 64, status="PENDING_REVIEW",
            sources_json=[{"variantId": str(ids["variant"])}], manifest_json={},
        ))
        await reserve_usd(
            s, user_id=ids["user"], kind="CUTFLOW",
            operation_id=manual_id, units=1,
        )
        await s.commit()
    assert await enqueue_completed_v2(db, settings, store) == 1
    assert await enqueue_completed_v2(db, settings, store) == 0
    async with db.session_factory() as s:
        usage = (await s.scalars(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
        ))).all()
        assert len(usage) == 2
        assert sorted(row.status for row in usage) == ["RELEASED", "RESERVED"]
        assert sum(row.amount_microusd for row in usage if row.status != "RELEASED") == 150_000
        assert len((await s.scalars(select(MultiCutflowTask))).all()) == 1


async def test_opt_out_creates_no_automatic_cut_reservation(handoff_env):
    db, _, _, ids = handoff_env
    await _prepare(handoff_env)
    async with db.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        parent.settings_json = {"autoSubmitToCutflow": False}
        await reserve_auto_cutflow(s, parent=parent, attempt_id=uuid.uuid4(), units=1)
        await s.commit()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert usage == []

async def test_historical_budget_miss_is_read_only_while_other_writer_active(handoff_env):
    import sqlite3

    db, settings, _, ids = handoff_env
    await _prepare(handoff_env)
    async with db.session_factory() as session:
        parent = await session.get(ParentTask, ids['task'])
        parent.settings_json = {**parent.settings_json, BUDGET_KEY: str(uuid.uuid4())}
        await session.commit()
    # An unrelated transaction already owns the reserved lock. A historical
    # parent with no held reservation must not attempt a zero-row UPDATE.
    writer = sqlite3.connect(db.engine.url.database, timeout=0.1)
    try:
        writer.execute('BEGIN IMMEDIATE')
        async with db.session_factory() as session:
            parent = await session.get(ParentTask, ids['task'])
            assert await release_unused_auto_cutflow(session, parent) is False
    finally:
        writer.rollback()
        writer.close()
