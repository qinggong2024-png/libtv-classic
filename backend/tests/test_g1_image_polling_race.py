from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.domain.models import (
    ImageGenerationBatch,
    ImageGenerationItem,
    ImageGenerationOutput,
)
from libtv_agent.services.image_generations import refresh_batch
from sqlalchemy import func, select, text, update
from test_image_quota_concurrency import post, world  # noqa: F401, F811


@pytest.mark.parametrize("query_error", [False, True])
@pytest.mark.parametrize("new_status", ["COMPLETED", "PROCESSING"])
async def test_stale_image_poll_preserves_independent_completion(
    world,  # noqa: F811
    query_error,
    new_status,
):  # noqa: F811
    app, factory, provider, users = world
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await post(client, users[0], "image-poll-race")
        assert response.status_code == 201

    async def query(task_id):
        async with factory() as other:
            await other.execute(
                update(ImageGenerationItem).values(
                    status=new_status, raw_response_json={"newer": True},
                    last_polled_at=datetime.now(UTC),
                )
            )
            await other.execute(update(ImageGenerationBatch).values(status=new_status))
            await other.commit()
        if query_error:
            raise RuntimeError("synthetic stale query error")
        return {"data": {"id": task_id, "status": "processing"}}

    provider.get_prediction = query
    async with factory() as stale:
        batch = await stale.scalar(select(ImageGenerationBatch))
        await refresh_batch(stale, app=app, batch=batch)
    async with factory() as verify:
        item = await verify.scalar(select(ImageGenerationItem))
        batch = await verify.scalar(select(ImageGenerationBatch))
        assert item.status == new_status
        assert item.raw_response_json == {"newer": True}
        assert item.error_json is None
        assert batch.status == new_status
    assert len(provider.submissions) == 1


async def test_two_preloaded_sessions_materialize_once(world, monkeypatch):  # noqa: F811
    app, factory, provider, users = world
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await post(client, users[0], "image-double-poll")).status_code == 201
    downloads = []

    async def query(task_id):
        return {
            "data": {
                "id": task_id,
                "status": "completed",
                "outputs": ["https://synthetic.example.test/output.png"],
            }
        }

    async def materialize(app, output):
        # A real second DB session must be able to write while CDN I/O is
        # stalled. Before the fix, refresh_batch held the writer until return.
        async with factory() as independent:
            if independent.bind.dialect.name == "sqlite":
                await independent.execute(text("PRAGMA busy_timeout=250"))
                await independent.execute(text("BEGIN IMMEDIATE"))
                await independent.rollback()
            persisted = await independent.scalar(select(ImageGenerationOutput))
            assert persisted is not None and persisted.object_key is None
        downloads.append(output.id)
        output.object_key = "synthetic/completed.png"

    provider.get_prediction = query
    monkeypatch.setattr("libtv_agent.services.image_generations._materialize_output", materialize)
    async with factory() as first, factory() as second:
        batch1 = await first.scalar(select(ImageGenerationBatch))
        batch2 = await second.scalar(select(ImageGenerationBatch))
        await refresh_batch(first, app=app, batch=batch1)
        await refresh_batch(second, app=app, batch=batch2)
        assert batch2.status == "COMPLETED"
    async with factory() as verify:
        assert await verify.scalar(select(func.count()).select_from(ImageGenerationOutput)) == 1
    assert len(downloads) == 1
    assert len(provider.submissions) == 1


async def test_materialization_does_not_overwrite_old_api_winner(world, monkeypatch):  # noqa: F811
    app, factory, provider, users = world
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await post(client, users[0], "image-mixed-api-poll")).status_code == 201

    async def query(task_id):
        return {"data": {"id": task_id, "status": "completed",
                         "outputs": ["https://synthetic.example.test/result.png"]}}

    async def materialize(app, output):
        # Simulate a request on the draining API winning during CDN I/O.
        async with factory() as other:
            await other.execute(update(ImageGenerationOutput).where(
                ImageGenerationOutput.id == output.id,
            ).values(object_key="original/winner.png", sha256="a" * 64))
            await other.execute(update(ImageGenerationItem).values(
                status="COMPLETED", last_polled_at=datetime.now(UTC),
            ))
            await other.commit()
        output.object_key = "new/loser.png"
        output.sha256 = "b" * 64

    provider.get_prediction = query
    monkeypatch.setattr("libtv_agent.services.image_generations._materialize_output", materialize)
    async with factory() as session:
        batch = await session.scalar(select(ImageGenerationBatch))
        await refresh_batch(session, app=app, batch=batch)
    async with factory() as verify:
        output = await verify.scalar(select(ImageGenerationOutput))
        item = await verify.scalar(select(ImageGenerationItem))
        assert output.object_key == "original/winner.png" and output.sha256 == "a" * 64
        assert item.status == "COMPLETED"
    assert len(provider.submissions) == 1
