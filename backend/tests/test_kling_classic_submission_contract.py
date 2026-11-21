"""Regression checks for task-center admission with separate storyboard/product refs.

These checks use an isolated database and never call Kling, CUT or a paid provider.
"""
from __future__ import annotations

import uuid

import pytest
from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import (
    Base, GenerationAttempt, GenerationVariant, ParentTask, StagedUpload, User,
)
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services.async_video_submission import (
    PAYLOAD_KEY,
    accept_automatic_video_submission,
    pending_automatic_video_ids,
)
from libtv_agent.services.fixed_video_tasks import FixedVideoTaskValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_auto_admission_keeps_two_parent_tasks_and_reference_roles_isolated(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'task-admission.db').as_posix()}"
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = uuid.uuid4()
        settings = Settings(
            ai_adapter_mode="openai",
            openai_api_key="test-key-do-not-submit",
            _env_file=None,
        )
        cases = ((1, 1, 5), (3, 2, 15))
        created = []
        async with sessions() as session:
            session.add(User(
                id=owner_id,
                username="task-isolation-user",
                password_hash="unused",
                role=UserRole.USER,
            ))
            await session.flush()
            for case_index, (scene_count, product_count, duration) in enumerate(cases):
                scene_ids = []
                product_ids = []
                for role, count, output in (
                    ("scene", scene_count, scene_ids),
                    ("product", product_count, product_ids),
                ):
                    for index in range(1, count + 1):
                        upload = StagedUpload(
                            id=uuid.uuid4(),
                            user_id=owner_id,
                            original_filename=f"{case_index}-{role}-{index}.jpg",
                            sha256=f"{case_index + 1:x}{index:x}".ljust(64, "a"),
                            mime_type="image/jpeg",
                            size_bytes=1280,
                            width=720,
                            height=1280,
                            object_key=f"test/{case_index}/{role}/{index}.jpg",
                            thumbnail_key=f"test/{case_index}/{role}/{index}-thumb.jpg",
                        )
                        session.add(upload)
                        output.append(str(upload.id))
                await session.flush()
                payload = VideoTaskCreate.model_validate({
                    "clientTaskId": f"cli-admission-{case_index}",
                    "title": f"Isolated reference test {case_index}",
                    "storyboards": [
                        {"uploadId": item, "order": i + 1}
                        for i, item in enumerate(scene_ids)
                    ],
                    "subjectImages": [
                        {"uploadId": item, "order": i + 1}
                        for i, item in enumerate(product_ids)
                    ],
                    "generationMode": "automatic",
                    "siteProfileId": "tokyotiger",
                    "resultCount": 2,
                    "durationSeconds": duration,
                    "paidSubmissionConsent": True,
                })
                accepted = await accept_automatic_video_submission(
                    session, user_id=owner_id, payload=payload, settings=settings,
                )
                created.append((accepted.id, payload, scene_ids, product_ids, duration))
                await session.commit()

        async with sessions() as session:
            pending = set(await pending_automatic_video_ids(session, limit=10))
            assert pending == {task_id for task_id, *_ in created}
            assert await session.scalar(
                select(func.count()).select_from(GenerationAttempt)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(GenerationVariant)
            ) == 0
            for task_id, payload, scenes, products, duration in created:
                task = await session.get(ParentTask, task_id)
                assert task is not None
                assert task.analysis_stage == "QUEUED"
                assert task.asset_count == len(scenes) + len(products)
                snapshot = task.settings_json[PAYLOAD_KEY]
                assert snapshot["durationSeconds"] == duration
                assert snapshot["resultCount"] == 2
                assert [item["uploadId"] for item in snapshot["storyboards"]] == scenes
                assert [item["uploadId"] for item in snapshot["subjectImages"]] == products
                claimed = list(await session.scalars(
                    select(StagedUpload).where(StagedUpload.claimed_task_id == task_id)
                ))
                assert {str(row.id) for row in claimed} == set(scenes + products)
                assert len(claimed) == len(scenes) + len(products)
            with pytest.raises(FixedVideoTaskValidationError) as error:
                await accept_automatic_video_submission(
                    session, user_id=owner_id,
                    payload=created[0][1], settings=settings,
                )
            assert error.value.code == "STORYBOARD_UPLOAD_UNAVAILABLE"
            assert await session.scalar(
                select(func.count()).select_from(ParentTask)
            ) == len(created)
    finally:
        await engine.dispose()



@pytest.mark.asyncio
async def test_auto_cli_element_unsupported_before_claim_or_provider_call():
    payload = VideoTaskCreate.model_validate({
        "clientTaskId": "cli-unsupported-element",
        "title": "Do not accept unsupported historical Element",
        "storyboards": [{"uploadId": str(uuid.uuid4()), "order": 1}],
        "elementIds": ["historical-element-without-cli-binding"],
        "generationMode": "automatic",
        "siteProfileId": "tokyotiger",
        "durationSeconds": 5,
        "paidSubmissionConsent": True,
    })
    settings = Settings(
        ai_adapter_mode="openai",
        openai_api_key="test-key-do-not-submit",
        kling_provider_backend="cli",
        _env_file=None,
    )
    # No database/session is supplied: rejection must happen before DB access,
    # upload claims, AI planning, any CLI submission or billable work.
    with pytest.raises(FixedVideoTaskValidationError) as error:
        await accept_automatic_video_submission(
            None, user_id=uuid.uuid4(), payload=payload, settings=settings,
        )
    assert error.value.code == "KLING_ELEMENTS_UNSUPPORTED"
