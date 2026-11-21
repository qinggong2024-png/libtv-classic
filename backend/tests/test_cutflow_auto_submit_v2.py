"""Offline v2 auto-submission tests: no CUT/NAS/Provider calls."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import test_cutflow_auto_edit as old
import test_cutflow_handoff as shared
from libtv_agent.domain.enums import AttemptStatus, DownloadStatus, GenerationStatus, ValidationStatus, VariantKind
from libtv_agent.domain.models import (
    AccountUsage, CutflowHandoff, DownloadArtifact, GenerationAttempt,
    GenerationVariant, MultiCutflowTask, ParentTask, RuntimeConfigVersion, User,
)
from libtv_agent.services.cutflow_auto_submit_v2 import enqueue_completed_v2
from libtv_agent.services.cutflow_handoff import sha256_file
from sqlalchemy import select

base_handoff_env = shared.handoff_env
handoff_env = old.handoff_env


async def prepare(env):
    database, settings, _store, ids = env
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    settings.cutflow_multi_source_enabled = True
    settings.cutflow_auto_edit_enabled = True
    settings.cutflow_auto_submit_after = datetime.now(UTC) - timedelta(hours=1)
    async with database.session_factory() as s:
        variant = await s.get(GenerationVariant, ids["variant"])
        variant.download_status = DownloadStatus.READY
        artifact = await s.get(DownloadArtifact, ids["artifact"])
        artifact.size_bytes = len(b"generated-video")
        artifact.duration_seconds = 10.0
        artifact.width = 1080
        artifact.height = 1920
        actor = await s.get(User, ids["user"])
        actor.daily_budget_microusd = 20_000_000
        parent = await s.get(ParentTask, ids["task"])
        parent.settings_json = {**(parent.settings_json or {}), "autoSubmitToCutflow": True}
        s.add(RuntimeConfigVersion(
            version=1,
            config_json={"globalUsagePricingMicrousd": {"cutflowPriceUsd": 150_000}},
            change_reason="isolated v2 auto-edit test pricing",
        ))
        await s.commit()


@pytest.mark.parametrize("multiple", [False, True])
async def test_task_opt_out_skips_unified_auto_but_preserves_manual_send(handoff_env, tmp_path, multiple):
    from libtv_agent.services.cutflow_multi import create_multi_task

    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    source_ids = [ids["variant"]]
    async with database.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        parent.settings_json = {"autoSubmitToCutflow": False}
        if multiple:
            variant_id, attempt_id = uuid.uuid4(), uuid.uuid4()
            media = tmp_path / "second.mp4"
            media.write_bytes(b"another-real-video")
            key = await store.put_file(media, "outputs/optout.mp4", "video/mp4")
            s.add(GenerationVariant(
                id=variant_id, parent_task_id=ids["task"],
                variant_type=VariantKind.DYNAMIC, variant_index=2,
                generation_status=GenerationStatus.SUCCEEDED,
                download_status=DownloadStatus.READY,
                validation_status=ValidationStatus.PASSED,
            ))
            s.add(GenerationAttempt(
                id=attempt_id, variant_id=variant_id,
                provider="kling", status=AttemptStatus.SUCCEEDED,
            ))
            s.add(DownloadArtifact(
                id=uuid.uuid4(), attempt_id=attempt_id,
                status=DownloadStatus.READY, object_key=key,
                sha256=sha256_file(media), size_bytes=media.stat().st_size,
                duration_seconds=10.0, width=1080, height=1920, revision=1,
            ))
            source_ids.append(variant_id)
        await s.commit()

    assert await enqueue_completed_v2(database, settings, store) == 0
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []
        assert (await s.scalars(select(AccountUsage).where(
            AccountUsage.kind == "CUTFLOW",
        ))).all() == []
        manual = await create_multi_task(
            s, ids["user"], source_ids, "manual-after-optout", True,
            allow_single_source=not multiple,
        )
        await s.commit()
        assert len(manual.sources_json) == len(source_ids)


async def test_does_not_run_without_cutover(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    settings.cutflow_auto_submit_after = None
    assert await enqueue_completed_v2(database, settings, store) == 0
    settings.cutflow_auto_submit_after = datetime.now(UTC) + timedelta(days=1)
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []


async def test_auto_single_uses_one_source_v2_and_reserves_once(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    assert await enqueue_completed_v2(database, settings, store) == 1
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        tasks = (await s.scalars(select(MultiCutflowTask))).all()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(tasks) == 1
        assert tasks[0].idempotency_key == f"auto-unified-v2:{ids['task']}"
        assert tasks[0].status == "QUEUED"
        assert len(tasks[0].sources_json) == 1
        assert len(tasks[0].sources_json[0]["productReferences"]) == 1
        assert len(usage) == 1 and usage[0].units == 1
        assert (await s.scalars(select(CutflowHandoff))).all() == []


async def test_two_sources_group_into_one_edit(handoff_env, tmp_path):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    second_id = uuid.uuid4()
    data = tmp_path / "second.mp4"
    data.write_bytes(b"second-real-video")
    key = await store.put_file(data, "outputs/second.mp4", "video/mp4")
    async with database.session_factory() as s:
        s.add(GenerationVariant(
            id=second_id, parent_task_id=ids["task"],
            variant_type=VariantKind.DYNAMIC, variant_index=2,
            generation_status=GenerationStatus.SUCCEEDED,
            download_status=DownloadStatus.READY,
            validation_status=ValidationStatus.PASSED,
        ))
        attempt_id = uuid.uuid4()
        s.add(GenerationAttempt(
            id=attempt_id, variant_id=second_id,
            provider="kling", status=AttemptStatus.SUCCEEDED,
        ))
        s.add(DownloadArtifact(
            id=uuid.uuid4(), attempt_id=attempt_id,
            status=DownloadStatus.READY, object_key=key,
            sha256=sha256_file(data), size_bytes=data.stat().st_size,
            duration_seconds=10.0, width=1080, height=1920, revision=1,
        ))
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 1
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        tasks = (await s.scalars(select(MultiCutflowTask))).all()
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(tasks) == 1
        assert [item["order"] for item in tasks[0].sources_json] == [1, 2]
        refs = [ref["refId"] for source in tasks[0].sources_json for ref in source["productReferences"]]
        assert len(refs) == len(set(refs)) == 2
        assert len(usage) == 1 and usage[0].units == 2


async def test_pending_source_blocks_premature_group(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        s.add(GenerationVariant(
            id=uuid.uuid4(), parent_task_id=ids["task"],
            variant_type=VariantKind.DYNAMIC, variant_index=2,
            generation_status=GenerationStatus.RUNNING,
            download_status=DownloadStatus.NOT_READY,
            validation_status=ValidationStatus.NOT_READY,
        ))
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0


async def test_existing_manual_multi_prevents_auto_double_edit(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        s.add(MultiCutflowTask(
            id=uuid.uuid4(), user_id=ids["user"], idempotency_key="manual-old",
            payload_hash="a" * 64, status="PENDING_REVIEW",
            sources_json=[{"variantId": str(ids["variant"])}],
            manifest_json={}, output_json=[],
        ))
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0


async def test_auditor_may_not_be_auto_submitted(handoff_env):
    from libtv_agent.domain.enums import UserRole
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        actor = await s.get(User, ids["user"])
        actor.role = UserRole.AUDITOR
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0


async def test_budget_guard_is_not_bypassed(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        user = await s.get(User, ids["user"])
        user.daily_budget_microusd = 0
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []
        assert (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all() == []


async def test_existing_legacy_single_prevents_auto_duplicate(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    from libtv_agent.services.cutflow_handoff import create_cutflow_handoff
    async with database.session_factory() as s:
        await create_cutflow_handoff(
            s, settings=settings, object_store=store,
            user_id=ids["user"], task_id=ids["task"], variant_id=ids["variant"],
        )
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []


async def test_invalid_source_never_enters_live_v2_queue(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        variant = await s.get(GenerationVariant, ids["variant"])
        variant.validation_status = ValidationStatus.MANUAL_REVIEW
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []


async def test_disabled_cutflow_multi_feature_cannot_auto_enqueue(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    settings.cutflow_multi_source_enabled = False
    assert await enqueue_completed_v2(database, settings, store) == 0


async def test_uncapped_budget_still_reserves_usd_and_submits_once(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        user = await s.get(User, ids["user"])
        user.daily_budget_microusd = None
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 1
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert len((await s.scalars(select(MultiCutflowTask))).all()) == 1
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(usage) == 1 and usage[0].units == 1


async def test_admin_with_uncapped_budget_can_auto_edit_with_usd_reservation(handoff_env):
    from libtv_agent.domain.enums import UserRole
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        user = await s.get(User, ids["user"])
        user.role = UserRole.ADMIN
        user.daily_budget_microusd = None
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 1
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert len((await s.scalars(select(MultiCutflowTask))).all()) == 1
        usage = (await s.scalars(select(AccountUsage).where(AccountUsage.kind == "CUTFLOW"))).all()
        assert len(usage) == 1 and usage[0].units == 1


async def test_missing_auto_opt_in_never_schedules_paid_edit(handoff_env):
    database, settings, store, ids = handoff_env
    await prepare(handoff_env)
    async with database.session_factory() as s:
        parent = await s.get(ParentTask, ids["task"])
        parent.settings_json = {}
        await s.commit()
    assert await enqueue_completed_v2(database, settings, store) == 0
    async with database.session_factory() as s:
        assert (await s.scalars(select(MultiCutflowTask))).all() == []
