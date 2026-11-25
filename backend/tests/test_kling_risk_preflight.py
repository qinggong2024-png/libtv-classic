"""Synthetic history only: no remote provider calls and no paid submissions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from libtv_agent.domain.enums import AttemptStatus, VariantKind
from libtv_agent.domain.models import (
    Asset,
    Base,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    User,
)
from libtv_agent.services.kling_risk_control import repeated_product_risk_control
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = uuid.UUID("00000000-0000-0000-0000-000000000071")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000072")


async def _record(session, *, user_id=USER, images=("product-a",), errors=(),
                  successes=0, days_old=0):
    task = ParentTask(
        id=uuid.uuid4(), user_id=user_id, client_task_id=uuid.uuid4().hex,
        title="Synthetic prior task", asset_count=len(images),
        workflow_kind="FIXED_TEMPLATE",
        created_at=datetime.now(UTC) - timedelta(days=days_old),
    )
    session.add(task)
    await session.flush()
    for n, sha in enumerate(images):
        session.add(Asset(
            parent_task_id=task.id, role="PRODUCT", source_type="SUBJECT_REFERENCE",
            order_no=101 + n, sha256=sha, original_filename="garment.jpg",
            mime_type="image/jpeg", size_bytes=1024, width=320, height=320,
        ))
    for n, message in enumerate((*errors, *([""] * successes)), 1):
        variant = GenerationVariant(
            parent_task_id=task.id, variant_type=VariantKind.STABLE,
            variant_index=n, revision=n, prompt_text="non-sensitive clothing display",
        )
        session.add(variant)
        await session.flush()
        success = n > len(errors)
        session.add(GenerationAttempt(
            variant_id=variant.id,
            provider="kling_official_api",
            task_id=f"cloud-{uuid.uuid4().hex}",
            status=AttemptStatus.SUCCEEDED if success else AttemptStatus.FAILED_TERMINAL,
            last_error_code=(
                None if success else
                "KLING_REMOTE_TASK_FAILED"  # old callback misclassification
            ),
            error_message=message,
        ))
    await session.flush()


@pytest.mark.asyncio
async def test_preflight_blocks_only_two_confirmed_rejections_without_success(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'risk.db').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            User(id=USER, username="synthetic-user", password_hash="disabled"),
            User(id=OTHER, username="synthetic-other", password_hash="disabled"),
        ])
        await _record(session, errors=(
            "Failure to pass the risk control system",
            "Failure to pass the risk control system",
        ))
        await session.commit()
        blocked = await repeated_product_risk_control(
            session, user_id=USER, product_hashes=("product-a",)
        )
        assert blocked is not None
        assert blocked.failed_remote_task_count == 2
        assert await repeated_product_risk_control(
            session, user_id=USER, product_hashes=("product-b",)
        ) is None
        assert await repeated_product_risk_control(
            session, user_id=OTHER, product_hashes=("product-a",)
        ) is None
        assert await repeated_product_risk_control(
            session, user_id=USER, product_hashes=("product-a", "extra-image")
        ) is None

        await _record(session, successes=1)
        await session.commit()
        assert await repeated_product_risk_control(
            session, user_id=USER, product_hashes=("product-a",)
        ) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_preflight_ignores_uncertain_stale_and_unverified_failures(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'risk-stale.db').as_posix()}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(User(id=USER, username="synthetic-user", password_hash="disabled"))
        await _record(session, images=("product-x",), errors=(
            "Failure to pass the risk control system",
            "Failure to pass the risk control system",
        ), days_old=25)
        await _record(session, images=("product-y",), errors=(
            "Provider timed out", "Provider timed out",
        ))
        await session.commit()
        for product in ("product-x", "product-y"):
            assert await repeated_product_risk_control(
                session, user_id=USER, product_hashes=(product,)
            ) is None
    await engine.dispose()
