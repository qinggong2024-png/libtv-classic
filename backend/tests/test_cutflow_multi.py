"""Fail-closed and deterministic invariants for the multi-source task scaffold."""

import importlib.util
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import test_cutflow_handoff as shared
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.enums import (
    AttemptStatus,
    DownloadStatus,
    GenerationStatus,
    QualityDecisionStatus,
    ValidationStatus,
    VariantKind,
)
from libtv_agent.domain.models import (
    Asset,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    MultiCutflowTask,
)
from libtv_agent.services.cutflow_multi import (
    MAX_MULTI_CUTFLOW_SOURCES,
    MultiCutflowError,
    create_multi_task,
    snapshot_sources,
    source_payload_hash,
)
from sqlalchemy import func, select

handoff_env = shared.handoff_env


@pytest.fixture
async def product_reference(handoff_env):
    # These task-creation tests need the product binding required by CUT.
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        session.add(Asset(
            parent_task_id=ids["task"], role="PRODUCT", order_no=1,
            original_filename="product.jpg", mime_type="image/jpeg",
            object_key="products/product.jpg", sha256="a" * 64, size_bytes=100,
            width=100, height=100,
        ))
        await session.commit()


def test_source_order_is_part_of_idempotency_payload():
    first, second = uuid.uuid4(), uuid.uuid4()
    assert source_payload_hash([first, second]) != source_payload_hash([second, first])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_ids", "code"),
    [
        ([], "EMPTY_SOURCES"),
        ([uuid.uuid4()], "TOO_FEW_SOURCES"),
        (
            [
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
            ],
            "TOO_MANY_SOURCES",
        ),
    ],
)
async def test_invalid_source_count_rejected_before_database_reads(handoff_env, source_ids, code):
    async with handoff_env[0].session_factory() as session:
        with pytest.raises(MultiCutflowError) as error:
            await create_multi_task(session, handoff_env[3]["user"], source_ids, "key", True)
        assert error.value.code == code


@pytest.mark.asyncio
async def test_duplicate_sources_and_missing_confirmation_rejected(handoff_env):
    ids = handoff_env[3]
    async with handoff_env[0].session_factory() as session:
        with pytest.raises(MultiCutflowError, match="不能重复"):
            await create_multi_task(
                session, ids["user"], [ids["variant"], ids["variant"]], "key", True
            )
        with pytest.raises(MultiCutflowError, match="确认"):
            await create_multi_task(
                session, ids["user"], [uuid.uuid4(), uuid.uuid4()], "key", False
            )


@pytest.mark.asyncio
async def test_repeat_reference_matches_first_successful_batch_of_same_sources(
    handoff_env, product_reference,
):
    database, _settings, _store, ids = handoff_env
    migration_path = (
        Path(__file__).parents[1]
        / "alembic/versions/da734c1108af_multi_cutflow_tasks.py"
    )
    spec = importlib.util.spec_from_file_location("multi_cutflow_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def install_multi_table(connection):
        MultiCutflowTask.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    async with database.engine.begin() as connection:
        await connection.run_sync(install_multi_table)
    second = {key: uuid.uuid4() for key in ("variant", "attempt", "artifact")}
    async with database.session_factory() as session:
        first_artifact = await session.get(DownloadArtifact, ids["artifact"])
        first_artifact.size_bytes = 15
        first_artifact.duration_seconds = 3.5
        first_artifact.width = 720
        first_artifact.height = 1280
        session.add_all([
            GenerationVariant(
                id=second["variant"],
                parent_task_id=ids["task"],
                variant_type=VariantKind.DYNAMIC,
                generation_status=GenerationStatus.SUCCEEDED,
                download_status=DownloadStatus.READY,
                validation_status=ValidationStatus.PASSED,
                final_approval_status=QualityDecisionStatus.APPROVED,
            ),
            GenerationAttempt(
                id=second["attempt"],
                variant_id=second["variant"],
                status=AttemptStatus.SUCCEEDED,
                provider="kling",
            ),
            DownloadArtifact(
                id=second["artifact"],
                attempt_id=second["attempt"],
                status=DownloadStatus.READY,
                object_key=first_artifact.object_key,
                sha256=first_artifact.sha256,
                size_bytes=15,
                duration_seconds=3.5,
                width=720,
                height=1280,
                revision=1,
            ),
        ])
        await session.commit()

        sources = await snapshot_sources(
            session, ids["user"], [ids["variant"], second["variant"]],
        )

        assert [source["taskId"] for source in sources] == [str(ids["task"]), str(ids["task"])]
        assert [source["order"] for source in sources] == [1, 2]
        selected = [ids["variant"], second["variant"]]
        for key, owner, snapshots, outputs in (
            ("other-user", ids["other"], sources, [{"id": "other"}]),
            ("other-sources", ids["user"], sources[:1], [{"id": "subset"}]),
            ("missing-output", ids["user"], sources, []),
        ):
            session.add(MultiCutflowTask(
                user_id=owner, idempotency_key=key, payload_hash="d" * 64,
                status="PENDING_REVIEW", sources_json=snapshots, output_json=outputs,
                created_at=datetime(2020, 1, 1),
            ))
        await session.commit()

        first = await create_multi_task(
            session, ids["user"], selected, "first", True, include_repeat_reference=True,
        )
        assert "referenceBatchTaskId" not in first.manifest_json
        first.status = "PENDING_REVIEW"
        first.output_json = [{"id": "first-output"}]
        await session.commit()

        repeat = await create_multi_task(
            session, ids["user"], selected[::-1], "second", True, include_repeat_reference=True,
        )
        assert repeat.id != first.id
        assert repeat.manifest_json["referenceBatchTaskId"] == str(first.id)
        repeat.status = "PENDING_REVIEW"
        repeat.output_json = [{"id": "second-output"}]
        repeat.created_at = first.created_at + timedelta(seconds=1)
        await session.commit()

        third = await create_multi_task(
            session, ids["user"], selected, "third", True, include_repeat_reference=True,
        )
        assert third.id not in {first.id, repeat.id}
        assert third.manifest_json["referenceBatchTaskId"] == str(first.id)
        retried = await create_multi_task(session, ids["user"], selected, "third", True)
        assert retried.id == third.id
        assert retried.manifest_json == third.manifest_json


@pytest.mark.asyncio
async def test_single_source_can_create_a_new_revision_after_previous_edit_finished(
    handoff_env, product_reference,
):
    database, _settings, _store, ids = handoff_env
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        artifact.size_bytes = 15
        artifact.duration_seconds = 3.5
        artifact.width = 720
        artifact.height = 1280
        session.add(MultiCutflowTask(
            user_id=ids["user"],
            idempotency_key="completed-edit",
            payload_hash="d" * 64,
            status="PENDING_REVIEW",
            sources_json=[{"variantId": str(ids["variant"])}],
            manifest_json={"sources": []},
            output_json=[{"id": "old-render"}],
        ))
        await session.commit()

        with pytest.raises(MultiCutflowError, match="至少需要两条"):
            await create_multi_task(
                session, ids["user"], [ids["variant"]], "single-default", True,
            )
        revision = await create_multi_task(
            session,
            ids["user"],
            [ids["variant"]],
            "single-revision",
            True,
            allow_single_source=True,
        )

    assert revision.status == "QUEUED"
    assert "referenceBatchTaskId" not in revision.manifest_json
    assert [source["variantId"] for source in revision.sources_json] == [str(ids["variant"])]


@pytest.mark.asyncio
async def test_unready_cut_endpoint_does_not_create_orphan_task(handoff_env):
    database, settings, _store, ids = handoff_env
    settings.cutflow_multi_source_enabled = False
    app = create_app(settings)
    app.state.database = database
    body = {
        "sourceIds": [str(uuid.uuid4()), str(uuid.uuid4())],
        "idempotencyKey": "stable-test-key",
        "confirmPolicy": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cutflow-handoffs/multi", json=body, headers={"X-User-Id": str(ids["user"])}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CUTFLOW_MULTI_SOURCE_NOT_READY"
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MultiCutflowTask)) == 0


@pytest.mark.asyncio
async def test_enabled_flag_still_requires_lan_transport(handoff_env):
    database, settings, _store, ids = handoff_env
    settings.cutflow_multi_source_enabled = True
    app = create_app(settings)
    app.state.database = database
    body = {
        "sourceIds": [str(uuid.uuid4()), str(uuid.uuid4())],
        "idempotencyKey": "gate-test-key",
        "confirmPolicy": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/cutflow-handoffs/multi", json=body, headers={"X-User-Id": str(ids["user"])}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CUTFLOW_MULTI_SOURCE_NOT_READY"
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MultiCutflowTask)) == 0


@pytest.mark.asyncio
async def test_historical_isolated_task_cannot_replay_same_rejected_cut_id(handoff_env):
    database, settings, _store, ids = handoff_env
    settings.cutflow_multi_source_enabled = True
    settings.cutflow_transport = "lan"
    settings.cutflow_nas_status_enabled = True
    task_id = uuid.uuid4()
    async with database.session_factory() as session:
        session.add(MultiCutflowTask(
            id=task_id,
            user_id=ids["user"],
            idempotency_key=f"isolated-{task_id}",
            payload_hash="a" * 64,
            status="FAILED",
            error_code="CUTFLOW_HISTORICAL_TASK_ISOLATED",
            sources_json=[{"variantId": str(ids["variant"])}],
            manifest_json={"manifestSha256": "b" * 64},
            output_json=[],
        ))
        await session.commit()
    app = create_app(settings)
    app.state.database = database
    url = f"/api/v1/cutflow-handoffs/multi/{task_id}/retry"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url, headers={"X-User-Id": str(ids["user"])})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CUTFLOW_HISTORICAL_TASK_ISOLATED"
    async with database.session_factory() as session:
        saved = await session.get(MultiCutflowTask, task_id)
        assert saved.status == "FAILED"
        assert saved.error_code == "CUTFLOW_HISTORICAL_TASK_ISOLATED"


@pytest.mark.asyncio
async def test_multi_result_list_and_media_are_owner_scoped(handoff_env, monkeypatch):
    database, settings, store, ids = handoff_env
    task_id = uuid.uuid4()
    output_id = "result-1"
    media = settings.artifact_root / "fixture.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"returned-multi-result")
    object_key = await store.put_file(media, f"multi/{task_id}/{output_id}.mp4", "video/mp4")
    task = MultiCutflowTask(
        id=task_id,
        user_id=ids["user"],
        idempotency_key="existing-task",
        payload_hash="a" * 64,
        status="PENDING_REVIEW",
        sources_json=[
            {
                "clipId": "clip-1",
                "order": 1,
                "taskId": str(ids["task"]),
                "variantId": str(ids["variant"]),
                "artifactId": str(ids["artifact"]),
                "title": "Fixture source",
                "durationSeconds": 2.5,
                "objectKey": "must-not-leak",
            }
        ],
        manifest_json={"manifestSha256": "b" * 64},
        output_json=[
            {
                "id": output_id,
                "sha256": "c" * 64,
                "object_key": object_key,
                "size_bytes": media.stat().st_size,
                "duration_seconds": 1.25,
                "width": 1080,
                "height": 1920,
                "review_status": "PENDING",
                "created_at": "2026-10-29T00:00:00+00:00",
            }
        ],
        failure_count=0,
    )
    async with database.session_factory() as session:
        session.add(task)
        await session.commit()

    app = create_app(settings)
    app.state.database = database
    app.state.object_store = store

    async def localize(_key, _destination):
        return media

    monkeypatch.setattr(store, "localize", localize)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own = await client.get(
            "/api/v1/cutflow-handoffs/multi", headers={"X-User-Id": str(ids["user"])}
        )
        other = await client.get(
            "/api/v1/cutflow-handoffs/multi", headers={"X-User-Id": str(ids["other"])}
        )
        playback = await client.get(
            f"/api/v1/cutflow-handoffs/multi/{task_id}/outputs/{output_id}",
            headers={"X-User-Id": str(ids["user"])},
        )
        forbidden = await client.get(
            f"/api/v1/cutflow-handoffs/multi/{task_id}/outputs/{output_id}",
            headers={"X-User-Id": str(ids["other"])},
        )

    viewed_url = f"/api/v1/cutflow-handoffs/multi/{task_id}/outputs/{output_id}/viewed"
    assert own.json()["tasks"][0]["outputs"][0]["viewedAt"] is None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(viewed_url, headers={"X-User-Id": str(ids["other"])})).status_code == 404
        assert (await client.post(viewed_url.replace(output_id, "invalid"), headers={
            "X-User-Id": str(ids["user"]),
        })).status_code == 404
        first_view = await client.post(viewed_url, headers={"X-User-Id": str(ids["user"])})
        repeat_view = await client.post(viewed_url, headers={"X-User-Id": str(ids["user"])})
        viewed_list = await client.get("/api/v1/cutflow-handoffs/multi", headers={
            "X-User-Id": str(ids["user"]),
        })
    assert first_view.status_code == repeat_view.status_code == 200
    assert first_view.json()["viewedAt"] == repeat_view.json()["viewedAt"]
    assert viewed_list.json()["tasks"][0]["outputs"][0]["viewedAt"] == first_view.json()["viewedAt"]

    payload = own.json()["tasks"][0]
    assert payload["id"] == str(task_id)
    assert payload["sources"][0]["title"] == "Fixture source"
    assert payload["outputs"][0]["mediaUrl"].endswith(f"/multi/{task_id}/outputs/{output_id}")
    assert "object_key" not in payload["outputs"][0]
    assert "objectKey" not in payload["sources"][0]
    assert other.json()["tasks"] == []
    assert playback.status_code == 200
    assert playback.content == b"returned-multi-result"
    assert forbidden.status_code == 404


def test_source_limit_is_explicit():
    assert MAX_MULTI_CUTFLOW_SOURCES == 8
