"""Recent OOTD motion-history reads are narrow, tenant-scoped and read-only."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from libtv_agent.domain.enums import UserRole, VariantKind
from libtv_agent.domain.models import Base, GenerationVariant, ParentTask, User
from libtv_agent.services.fixed_video_tasks import _recent_ootd_prompts
from libtv_agent.services.ootd_simple import PROFILE, HISTORY_PROFILES
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_history_uses_recent_confirmed_distinct_tasks_of_same_user_and_site():
    assert HISTORY_PROFILES == ("allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2", PROFILE)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            now = datetime.now(UTC)
            user1 = User(id=uuid.uuid4(), username="historical-owner",
                         password_hash="fixture", role=UserRole.USER)
            user2 = User(id=uuid.uuid4(), username="historical-other",
                         password_hash="fixture", role=UserRole.USER)
            session.add_all([user1, user2])
            await session.flush()
            records = []

            def make_task(owner, brand, age_days, text, confirmed=True, version=PROFILE):
                task = ParentTask(
                    id=uuid.uuid4(), user_id=owner.id, client_task_id=str(uuid.uuid4()),
                    title="historic", asset_count=1, workflow_kind="FIXED_TEMPLATE",
                    site_profile_id=brand, created_at=now-timedelta(days=age_days),
                )
                variant = GenerationVariant(
                    id=uuid.uuid4(), parent_task_id=task.id,
                    variant_type=VariantKind.STABLE, revision=1,
                    prompt_text=text, prompt_template_version=version,
                    confirmed_at=now if confirmed else None,
                    created_at=now-timedelta(days=age_days),
                )
                session.add_all([task, variant])
                records.append((task, variant))
                return task

            current = make_task(user1, "nerdywave", 0, "current task")
            first = make_task(user1, "nerdywave", 0.2, "recent 1")
            make_task(user1, "nerdywave", 0.3, "recent 2", version="allwe-ootd-sol-simple-v1")
            make_task(user1, "nerdywave", 0.4, "recent 3")
            make_task(user1, "nerdywave", 0.5, "recent 4 excluded by limit")
            make_task(user2, "nerdywave", 0.1, "other user's prompt")
            make_task(user1, "pagewings", 0.1, "other brand prompt")
            make_task(user1, "nerdywave", 40, "too old")
            make_task(user1, "nerdywave", 0.1, "unconfirmed", confirmed=False)
            # Multiple variants of the same previous task should not crowd out
            # other parents when fetching examples.
            session.add(GenerationVariant(
                parent_task_id=first.id, variant_type=VariantKind.STABLE,
                revision=2, prompt_text="second version of recent 1",
                prompt_template_version=PROFILE, confirmed_at=now,
                created_at=now-timedelta(days=0.19),
            ))
            await session.commit()
            prompts = await _recent_ootd_prompts(
                session, user_id=user1.id, site_profile_id="nerdywave",
                exclude_task_id=current.id,
            )
            assert len(prompts) == 3
            assert set(prompts) == {
                "second version of recent 1", "recent 2", "recent 3"
            }
            # Reading for another user never returns this user's examples.
            theirs = await _recent_ootd_prompts(
                session, user_id=user2.id, site_profile_id="nerdywave",
                exclude_task_id=None,
            )
            assert theirs == ("other user's prompt",)
    finally:
        await engine.dispose()
