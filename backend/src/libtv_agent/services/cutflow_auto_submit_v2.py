"""Opt-in, forward-only automatic CUT v2 submissions.

One canonical package per new parent task: a single finished source uses the
same v2 sources[] envelope as a completed group of 2-8 sources. Neither legacy
handoffs nor human review decisions are altered here.
"""
from __future__ import annotations

from fastapi import HTTPException
from libtv_agent.services.cutflow_auto_eligibility import eligible_auto_variants
from libtv_agent.config import Settings
from libtv_agent.domain.enums import ParentTaskPhase, UserRole
from libtv_agent.domain.models import (
    CutflowHandoff,
    GenerationVariant,
    MultiCutflowTask,
    ParentTask,
    User,
)
from libtv_agent.services.cutflow_budget import (
    bind_prepaid_cutflow,
    release_unused_auto_cutflow,
)
from libtv_agent.services.cutflow_multi import MultiCutflowError, create_multi_task
from libtv_agent.services.cutflow_multi_worker import multi_worker_ready
from libtv_agent.services.page_access import page_permissions
from sqlalchemy import select


async def enqueue_completed_v2(database, settings: Settings, _object_store) -> int:
    """Create at most one new auto-edit per scan; no retroactive submissions."""
    if not (settings.cutflow_auto_edit_enabled
            and settings.cutflow_auto_submit_after is not None
            and multi_worker_ready(settings)):
        return 0

    async with database.session_factory() as session:
        parents = (await session.scalars(
            select(ParentTask)
            .where(ParentTask.created_at >= settings.cutflow_auto_submit_after)
            .order_by(ParentTask.created_at.desc(), ParentTask.id.desc())
            .limit(1000)
        )).all()
        # Newest first prevents old invalid/unready candidates starving new
        # work; the bounded window avoids a full historical scan every pulse.
        for parent in parents:
            # This background action can incur USD fees. Only explicit opt-in
            # on tasks after the release cutover authorizes automatic edits.
            if (parent.settings_json or {}).get("autoSubmitToCutflow") is not True:
                continue
            user = await session.get(User, parent.user_id)
            # Use the shared USD reservation path for admins / uncapped users;
            # explicit finite limits, permissions and charges remain enforced.
            if (user is None or not user.is_active
                    or (user.daily_budget_microusd is not None
                        and user.daily_budget_microusd <= 0)
                    or user.role == UserRole.AUDITOR
                    or "make" not in page_permissions(user)):
                continue

            variants = (await session.scalars(
                select(GenerationVariant)
                .where(GenerationVariant.parent_task_id == parent.id)
                .order_by(GenerationVariant.variant_index, GenerationVariant.id)
            )).all()
            if not 1 <= len(variants) <= 8:
                continue
            # A terminal failure can exclude that original without losing its
            # usable siblings. Unknown and in-progress results still block.
            selected = eligible_auto_variants(variants, parent.phase)
            if parent.phase == ParentTaskPhase.CANCELLED or selected == []:
                if await release_unused_auto_cutflow(session, parent):
                    await session.commit()
                    return 1
                continue
            if selected is None:
                continue

            variant_ids = {variant.id for variant in variants}
            if (await session.scalar(
                select(CutflowHandoff.id)
                .where(CutflowHandoff.variant_id.in_(variant_ids))
                .limit(1)
            )) is not None:
                if await release_unused_auto_cutflow(session, parent):
                    await session.commit()
                    return 1
                continue
            # Completed as well as in-flight edits count as existing work.
            # Unlike retries, the daily auto path never edits a source twice.
            existing = (await session.scalars(
                select(MultiCutflowTask)
                .where(MultiCutflowTask.user_id == parent.user_id)
            )).all()
            if any(
                str(source.get("variantId")) in {str(x) for x in variant_ids}
                for prior in existing for source in prior.sources_json
            ):
                if await release_unused_auto_cutflow(session, parent):
                    await session.commit()
                    return 1
                continue

            try:
                async with session.begin_nested():
                    task = await create_multi_task(
                        session, parent.user_id,
                        [variant.id for variant in selected],
                        f"auto-unified-v2:{parent.id}", True,
                        allow_single_source=len(selected) == 1,
                    )
                    await bind_prepaid_cutflow(
                        session, parent=parent, cutflow_task=task,
                        units=len(selected),
                    )
            except (MultiCutflowError, HTTPException):
                # Invalid inputs, exhausted/unknown USD budget, or no real
                # product reference are non-permissions: skip, never bypass.
                continue
            await session.commit()
            return 1
    return 0
