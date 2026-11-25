"""Presentation acknowledgement must not mutate execution or leak between users."""
import uuid

import pytest
from fastapi import HTTPException
from libtv_agent.api.tasks import (
    TaskAttentionAckRequest,
    delete_task_attention_acknowledgement,
    list_task_attention_acknowledgements,
    put_task_attention_acknowledgement,
)
from libtv_agent.db import Database
from libtv_agent.domain.enums import ParentTaskPhase
from libtv_agent.domain.models import Base, ParentTask, TaskAttentionAcknowledgement, User
from sqlalchemy import select


async def test_acknowledgement_is_per_user_idempotent_and_preserves_task_phase():
    database = Database("sqlite+aiosqlite:///:memory:")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            user_a = User(id=uuid.uuid4(), username="ack-owner", password_hash="disabled")
            user_b = User(id=uuid.uuid4(), username="ack-other", password_hash="disabled")
            task = ParentTask(
                id=uuid.uuid4(), user_id=user_a.id, client_task_id="ack-task",
                title="historical failed video", asset_count=0,
                phase=ParentTaskPhase.ACTION_REQUIRED,
            )
            session.add_all([user_a, user_b, task])
            await session.commit()

            first = await put_task_attention_acknowledgement(
                task.id, TaskAttentionAckRequest(signature="v1-1234abcd"), user_a, session
            )
            assert first.status_code == 204
            await put_task_attention_acknowledgement(
                task.id, TaskAttentionAckRequest(signature="v1-5678abcd"), user_a, session
            )
            assert await list_task_attention_acknowledgements(user_a, session) == [
                {"taskId": str(task.id), "signature": "v1-5678abcd"}
            ]
            assert await list_task_attention_acknowledgements(user_b, session) == []
            stored = await session.scalar(
                select(TaskAttentionAcknowledgement)
                .where(TaskAttentionAcknowledgement.user_id == user_a.id)
            )
            assert stored is not None
            assert (await session.get(ParentTask, task.id)).phase == ParentTaskPhase.ACTION_REQUIRED

            with pytest.raises(HTTPException) as error:
                await put_task_attention_acknowledgement(
                    task.id, TaskAttentionAckRequest(signature="v1-99999999"), user_b, session
                )
            assert error.value.status_code == 404

            await delete_task_attention_acknowledgement(task.id, user_a, session)
            assert await list_task_attention_acknowledgements(user_a, session) == []
            assert (await session.get(ParentTask, task.id)).phase == ParentTaskPhase.ACTION_REQUIRED
    finally:
        await database.dispose()
