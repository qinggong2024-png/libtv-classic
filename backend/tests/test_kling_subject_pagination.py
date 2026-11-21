"""Isolated HTTP regressions for paged Kling subject access (no paid calls)."""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import Base, ParentTask, SubjectElementJob, User
from sqlalchemy.ext.asyncio import create_async_engine


class PagedKlingAdapter:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.provider_ids = [f"remote-{index:03d}" for index in range(123)]

    async def query_element(self, *, task_id=None, external_task_id=None):
        assert external_task_id is None
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.001)
            if task_id == "owned-003":
                raise RuntimeError("temporary provider timeout")
            return {"data": {"id": task_id, "name": task_id, "status": "ready"}}
        finally:
            self.active -= 1

    async def list_elements(self, *, page_num, page_size, official):
        ids = [] if official else self.provider_ids
        offset = (page_num - 1) * page_size
        return {
            "data": {
                "elements": [
                    {"id": item, "name": item, "status": "ready"}
                    for item in ids[offset:offset + page_size]
                ],
                "total": len(ids),
            }
        }


@pytest.mark.asyncio
async def test_subject_library_paging_isolation_and_provider_degradation(tmp_path):
    path = tmp_path / "library.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
        object_store_mode="local",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        _env_file=None,
    )
    app = create_app(settings)
    adapter = PagedKlingAdapter()
    owner = uuid.uuid4()
    other = uuid.uuid4()
    admin = uuid.UUID(settings.dev_admin_id)
    async with app.router.lifespan_context(app):
        app.state.kling_adapter = adapter
        async with app.state.database.session_factory() as session:
            session.add_all([
                User(id=owner, username="owner", password_hash="disabled", role=UserRole.USER),
                User(id=other, username="other", password_hash="disabled", role=UserRole.USER),
            ])
            await session.flush()
            for index in range(54):
                uid = owner if index < 53 else other
                task = ParentTask(
                    id=uuid.uuid4(),
                    user_id=uid,
                    client_task_id=f"subject-{index}",
                    title=f"Subject {index}",
                    asset_count=1,
                )
                session.add(task)
                session.add(SubjectElementJob(
                    parent_task_id=task.id,
                    user_id=uid,
                    external_task_id=f"ext-{index}",
                    element_id=f"owned-{index:03d}",
                    name=f"Owned {index}",
                    description="owned subject",
                    status="SUCCEEDED",
                ))
            for index in range(3):
                task = ParentTask(
                    id=uuid.uuid4(),
                    user_id=admin,
                    client_task_id=f"pending-{index}",
                    title=f"Pending {index}",
                    asset_count=1,
                )
                session.add(task)
                session.add(SubjectElementJob(
                    parent_task_id=task.id,
                    user_id=admin,
                    external_task_id=f"pending-{index}",
                    name=f"Pending {index}",
                    description="local pending job",
                    status="SUBMITTED" if index == 2 else "PROCESSING",
                ))
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-User-Id": str(owner)}
            pages = []
            for number in (1, 2, 3):
                response = await client.get(
                    "/api/v1/library/kling/elements",
                    headers=headers,
                    params={"pageNum": number, "pageSize": 20},
                )
                assert response.status_code == 200, response.text
                assert response.json()["total"] == 53
                pages.extend(response.json()["items"])
            assert len(pages) == 53
            assert len({item["id"] for item in pages}) == 53
            assert "owned-053" not in {item["id"] for item in pages}
            unavailable = next(item for item in pages if item["id"] == "owned-003")
            assert unavailable["status"] == "unavailable"
            assert 2 <= adapter.max_active <= 5

            official = await client.get(
                "/api/v1/library/kling/elements",
                headers=headers,
                params={"official": "true"},
            )
            assert official.status_code == 200
            assert official.json()["items"] == []
            assert official.json()["total"] == 0

            admin_headers = {"X-User-Id": str(admin)}
            listed = []
            for number in range(1, 8):
                response = await client.get(
                    "/api/v1/library/kling/elements",
                    headers=admin_headers,
                    params={"pageNum": number, "pageSize": 20},
                )
                assert response.status_code == 200, response.text
                assert response.json()["total"] == 126
                listed.extend(item["id"] for item in response.json()["items"])
            assert len(listed) == 126
            assert len(set(listed)) == 126
            assert len([item for item in listed if item.startswith("local-job:")]) == 3
            assert set(adapter.provider_ids).issubset(listed)
