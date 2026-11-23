from __future__ import annotations

import base64
import hashlib
import io
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.api.image_generations import ImageBatchManifest, _expand_manifest
from libtv_agent.app import _refresh_active_image_generation_batches, create_app
from libtv_agent.config import Settings
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import (
    Base,
    ImageGenerationBatch,
    ImageGenerationItem,
    RuntimeConfigVersion,
    User,
)
from libtv_agent.services.auth import PasswordService
from libtv_agent.services.image_generations import (
    ImageBatchSubmissionError,
    _prediction_result,
    _submission_result,
    _validate_public_https_url,
)
from libtv_agent.services.runtime_control import get_runtime_config, update_runtime_config
from libtv_agent.templates.garment_materials import garment_material_guidance
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine


class FakeWaveSpeedAdapter:
    def __init__(self, *, submit_error: Exception | None = None) -> None:
        self.submit_error = submit_error
        self.uploads: list[Path] = []
        self.submissions: list[tuple[str, dict[str, Any]]] = []
        self.queries: list[str] = []

    async def upload_media(self, path: Path) -> str:
        self.uploads.append(path)
        return f"https://media.example.test/{path.name}"

    async def submit_prediction(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.submissions.append((model_id, payload))
        if self.submit_error is not None:
            raise self.submit_error
        return {
            "data": {
                "id": f"prediction-{len(self.submissions)}",
                "status": "created",
                "urls": {"get": "https://api.example.test/prediction/result"},
            }
        }

    async def get_prediction(self, task_id: str) -> dict[str, Any]:
        self.queries.append(task_id)
        return {"data": {"id": task_id, "status": "processing", "outputs": []}}

    def availability_snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(available=True, status="WAVESPEED_READY", reason=None)

    async def verify_credentials(self) -> SimpleNamespace:
        return self.availability_snapshot()


async def _app(tmp_path: Path) -> tuple[Any, Settings]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'images.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    settings = Settings(
        app_env="test",
        database_url=database_url,
        object_store_mode="local",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        wavespeed_enabled=True,
        wavespeed_api_key="test-only-key",
        global_paused=False,
        paid_submissions_paused=False,
        _env_file=None,
    )
    settings.runner_root.mkdir(parents=True)
    app = create_app(settings)
    app.state.wavespeed_adapter = FakeWaveSpeedAdapter()
    return app, settings


async def _production_app(
    tmp_path: Path,
    *,
    user_id: uuid.UUID,
    pilot_user_ids: list[str],
    pilot_budget_limit: int,
) -> tuple[Any, Settings]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'production-images.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    settings = Settings(
        app_env="production",
        shared_authentication=False,
        database_url=database_url,
        object_store_mode="local",
        artifact_root=tmp_path / "artifacts",
        runner_root=tmp_path / "runner",
        wavespeed_enabled=True,
        wavespeed_api_key="test-only-key",
        global_paused=False,
        paid_submissions_paused=False,
        session_secret="pilot-test-session-secret",
        minio_secret_key="pilot-test-minio-secret",
        object_store_prefix="pilot-test",
        libtv_workspace_id="pilot-test-workspace",
        libtv_project_uuid="pilot-test-project",
        admin_credentials_file=tmp_path / "runtime" / "admin-credentials.json",
        kling_admin_config_file=tmp_path / "runtime" / "kling-config.json",
        wavespeed_admin_config_file=tmp_path / "runtime" / "wavespeed-config.json",
        bach_admin_config_file=tmp_path / "runtime" / "bach-config.json",
        _env_file=None,
    )
    settings.runner_root.mkdir(parents=True)
    app = create_app(settings)
    app.state.wavespeed_adapter = FakeWaveSpeedAdapter()
    async with app.router.lifespan_context(app):
        async with app.state.database.session_factory() as session:
            session.add(
                User(
                    id=user_id,
                    username="pilot-user",
                    password_hash=PasswordService().hash("pilot-password"),
                    role=UserRole.USER,
                )
            )
            await session.commit()
            current = await get_runtime_config(session, settings)
            assert current is not None
            config = dict(current.config_json)
            config.update(
                {
                    "internalPilotEnabled": True,
                    "pilotUserIds": pilot_user_ids,
                    "pilotBudgetLimit": pilot_budget_limit,
                    "pilotSubmittedCount": 0,
                }
            )
            await update_runtime_config(
                session,
                settings,
                config=config,
                actor_id=None,
                reason="test pilot configuration",
                expected_version=current.version,
            )
            await session.commit()
    return app, settings


def _manifest(*, name: str = "catalog", expected: int = 1) -> dict[str, Any]:
    return {
        "clientBatchId": "client-batch-1",
        "name": name,
        "provider": "nanobanana",
        "modes": ["fusion"],
        "resultCount": 1,
        "aspectRatio": "9:16",
        "resolution": "2k",
        "outputFormat": "png",
        "allowBackInference": True,
        "paidSubmissionConsent": True,
        "expectedGenerationCount": expected,
        "items": [
            {
                "clientTaskId": "client-item-1",
                "outfitId": "outfit-1",
                "outfitName": "Look 1",
                "sku": "SKU-1",
                "prompts": {"fusion": "Keep the garment exact.", "storyboard": ""},
                "referenceFileIndexes": [0, 1],
                "referenceRoles": [
                    {"fileIndex": 0, "role": "model.front"},
                    {"fileIndex": 1, "role": "product.front"},
                ],
            }
        ],
    }


def _multipart(manifest: dict[str, Any]) -> tuple[dict[str, str], list[tuple[str, Any]]]:
    import json

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    file_count = (
        max(
            reference["fileIndex"]
            for item in manifest["items"]
            for reference in item.get("referenceRoles", [])
        )
        + 1
    )
    return (
        {"clientManifest": json.dumps(manifest)},
        [("files", (f"reference-{index}.png", png, "image/png")) for index in range(file_count)],
    )


def test_manifest_rejects_multiple_generation_modes() -> None:
    manifest = _manifest()
    manifest["modes"] = ["fusion", "storyboard"]

    with pytest.raises(ValueError, match="at most 1 item"):
        ImageBatchManifest.model_validate(manifest)


def test_single_image_storyboard_prompt_keeps_fixed_reference_roles() -> None:
    manifest = ImageBatchManifest.model_validate(
        {
            "clientBatchId": "storyboard-batch",
            "name": "storyboard",
            "provider": "nanobanana",
            "modes": ["storyboard"],
            "resultCount": 1,
            "paidSubmissionConsent": True,
            "expectedGenerationCount": 1,
            "items": [
                {
                    "clientTaskId": "storyboard-item",
                    "outfitId": "outfit-1",
                    "outfitName": "Look 1",
                    "prompts": {
                        "fusion": "",
                        "storyboard": (
                            "根据图一的人物和环境，生成一张新姿势图片，"
                            "正面与图二一致，背面与图三一致"
                        ),
                    },
                    "referenceFileIndexes": [0, 1, 2],
                    "referenceRoles": [
                        {"fileIndex": 0, "role": "model.front"},
                        {"fileIndex": 1, "role": "product.front"},
                        {"fileIndex": 2, "role": "product.back"},
                    ],
                }
            ],
        }
    )

    specs = _expand_manifest(manifest, file_count=3)

    assert len(specs) == 1
    assert "Shot 1" in specs[0].prompt

    manifest.items[0].referenceRoles.pop()
    manifest.items[0].referenceFileIndexes.pop()
    with pytest.raises(ValueError, match="no product back image"):
        _expand_manifest(manifest, file_count=2)


def test_user_requirement_is_a_separate_optional_priority_section() -> None:
    manifest_data = _manifest()
    manifest_data["items"][0]["userRequirement"] = "换成室内背景"
    manifest = ImageBatchManifest.model_validate(manifest_data)

    spec = _expand_manifest(manifest, file_count=2)[0]

    assert "基础生成要求：\nKeep the garment exact." in spec.prompt
    assert "当前任务要求：" in spec.prompt
    assert "用户补充要求（优先）：\n换成室内背景" in spec.prompt
    assert "以用户补充要求为准" in spec.prompt


def test_empty_user_requirement_keeps_prompt_without_priority_wrapper() -> None:
    manifest = ImageBatchManifest.model_validate(_manifest())

    spec = _expand_manifest(manifest, file_count=2)[0]

    assert spec.prompt.startswith("Keep the garment exact.")
    assert "基础生成要求：" not in spec.prompt
    assert "用户补充要求" not in spec.prompt


def test_storyboard_user_requirement_is_separate_from_base_and_shot_instruction() -> None:
    manifest = ImageBatchManifest.model_validate(
        {
            "clientBatchId": "storyboard-priority-batch",
            "name": "storyboard",
            "provider": "nanobanana",
            "modes": ["storyboard"],
            "resultCount": 1,
            "paidSubmissionConsent": True,
            "expectedGenerationCount": 1,
            "items": [
                {
                    "clientTaskId": "storyboard-priority-item",
                    "outfitId": "outfit-1",
                    "outfitName": "Look 1",
                    "prompts": {"fusion": "", "storyboard": "保持人物与服装细节"},
                    "userRequirement": "图一为正面，图二为背面",
                    "storyboardShots": [{"order": 1, "instruction": "展示背面细节"}],
                    "referenceFileIndexes": [0, 1],
                    "referenceRoles": [
                        {"fileIndex": 0, "role": "model.reference"},
                        {"fileIndex": 1, "role": "product.reference"},
                    ],
                }
            ],
        }
    )

    spec = _expand_manifest(manifest, file_count=2)[0]

    assert "基础生成要求：\n保持人物与服装细节" in spec.prompt
    assert "当前任务要求：\nShot 1: 展示背面细节" in spec.prompt
    assert "用户补充要求（优先）：\n图一为正面，图二为背面" in spec.prompt
    assert "以用户补充要求为准" in spec.prompt


def test_manifest_rejects_negative_and_accepts_six_nano_references() -> None:
    invalid_index = _manifest()
    invalid_index["items"][0]["referenceFileIndexes"][0] = -1
    invalid_index["items"][0]["referenceRoles"][0]["fileIndex"] = -1
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        ImageBatchManifest.model_validate(invalid_index)

    too_many = _manifest(expected=2)
    too_many["items"][0]["referenceFileIndexes"] = list(range(6))
    too_many["items"][0]["referenceRoles"] = [
        {"fileIndex": index, "role": role}
        for index, role in enumerate(
            [
                "model.front",
                "product.front",
                "product.back",
                "model.back",
                "product.side",
                "model.side",
            ]
        )
    ]
    manifest = ImageBatchManifest.model_validate(too_many)
    assert _expand_manifest(manifest, file_count=6)

    too_many["items"][0]["referenceFileIndexes"].append(6)
    with pytest.raises(ValueError, match="at most 6"):
        ImageBatchManifest.model_validate(too_many)


def test_provider_status_parsing_fails_closed() -> None:
    with pytest.raises(ImageBatchSubmissionError) as captured:
        _submission_result({"data": {"id": "prediction-1"}})
    assert captured.value.uncertain is True

    status, outputs, _raw, error = _prediction_result(
        {"data": {"id": "prediction-1", "outputs": []}}
    )
    assert status == "FAILED"
    assert outputs == []
    assert error is not None
    assert error["code"] == "WAVESPEED_RESPONSE_INVALID"


def test_output_urls_must_be_public_https() -> None:
    with pytest.raises(ValueError, match="public HTTPS"):
        _validate_public_https_url("http://cdn.example.test/output.png")
    with pytest.raises(ValueError, match="public addresses"):
        _validate_public_https_url("https://127.0.0.1/output.png")


def test_fusion_outputs_pair_front_and_back_references_independently() -> None:
    manifest = ImageBatchManifest.model_validate(
        {
            "clientBatchId": "fusion-batch",
            "name": "fusion",
            "provider": "nanobanana",
            "modes": ["fusion"],
            "resultCount": 1,
            "paidSubmissionConsent": True,
            "expectedGenerationCount": 2,
            "items": [
                {
                    "clientTaskId": "fusion-item",
                    "outfitId": "outfit-1",
                    "outfitName": "Look 1",
                    "prompts": {"fusion": "编辑图一，图一人物穿上图二的球衣", "storyboard": ""},
                    "referenceFileIndexes": [0, 1, 2, 3],
                    "referenceRoles": [
                        {"fileIndex": 0, "role": "model.front"},
                        {"fileIndex": 1, "role": "product.front"},
                        {"fileIndex": 2, "role": "product.back"},
                        {"fileIndex": 3, "role": "model.back"},
                    ],
                }
            ],
        }
    )

    specs = _expand_manifest(manifest, file_count=4)

    assert [(spec.view, spec.reference_file_indexes) for spec in specs] == [
        ("front", (0, 1)),
        ("back", (3, 2)),
    ]
    assert "图一为人物正面，图二为服装正面" in specs[0].prompt
    assert "图一为人物背面参考，图二为服装背面参考" in specs[1].prompt
    assert "必须完整保留图一原始背景与环境" in specs[0].prompt
    assert "只允许在服装覆盖区域做必要修改" in specs[0].prompt
    assert "必须完整保留图一原始背景与环境" in specs[1].prompt
    assert "禁止生成纯色、白色、灰色、透明或摄影棚背景" in specs[1].prompt


def test_inferred_back_keeps_the_source_scene_without_locking_the_front_pose() -> None:
    manifest = ImageBatchManifest.model_validate(
        {
            "clientBatchId": "inferred-back-batch",
            "name": "inferred-back",
            "provider": "nanobanana",
            "modes": ["fusion"],
            "resultCount": 1,
            "paidSubmissionConsent": True,
            "expectedGenerationCount": 2,
            "items": [
                {
                    "clientTaskId": "inferred-back-item",
                    "outfitId": "outfit-1",
                    "outfitName": "Look 1",
                    "prompts": {"fusion": "自然换装", "storyboard": ""},
                    "referenceFileIndexes": [0, 1, 2],
                    "referenceRoles": [
                        {"fileIndex": 0, "role": "model.front"},
                        {"fileIndex": 1, "role": "product.front"},
                        {"fileIndex": 2, "role": "product.back"},
                    ],
                }
            ],
        }
    )

    specs = _expand_manifest(manifest, file_count=3)

    assert specs[1].reference_file_indexes == (0, 2)
    assert "只为呈现背面而自然调整人物朝向" in specs[1].prompt
    assert "必须保留图一背景环境的内容、空间关系" in specs[1].prompt
    assert "身体姿态、手部" not in specs[1].prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gpt", "nanobanana"])
async def test_batch_submission_is_idempotent_owned_and_provider_specific(
    tmp_path: Path,
    provider: str,
) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "image-key-1"}
    manifest = _manifest()
    manifest["provider"] = provider
    if provider == "gpt":
        manifest["quality"] = "high"
    data, files = _multipart(manifest)

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201, created.text
            body = created.json()
            batch_id = body["id"]
            assert body["status"] == "running"
            assert len(body["items"]) == 1
            refs = body["items"][0]["referenceUrls"]
            assert len(refs["model"]) == 1
            assert len(refs["product"]) == 1
            for url in [*refs["model"], *refs["product"]]:
                original = await client.get(url, headers={"X-User-Id": settings.dev_user_id})
                assert original.status_code == 200
                assert original.headers["content-type"] == "image/png"
                assert original.content.startswith(b"\x89PNG")
                forbidden = await client.get(url, headers={"X-User-Id": settings.dev_admin_id})
                assert forbidden.status_code == 404
            assert adapter.submissions == [
                (
                    (
                        "openai/gpt-image-2.5-flare/edit"
                        if provider == "gpt"
                        else "google/nano-banana-2.1/edit"
                    ),
                    {
                        "prompt": (
                            "Keep the garment exact.\n\n"
                            "商品材质与结构要求：\n"
                            + garment_material_guidance("unknown")
                            + "\n\n"
                            "当前生成正面融合图：图一为人物正面，图二为服装正面。"
                            "以图一为唯一底图进行局部换装编辑，只替换人物身上的上衣为图二服装。"
                            "必须完整保留图一原始背景与环境，不得抠出人物，不得移除、替换、虚化、"
                            "扩展或重构背景；保持图一人物身份、脸部、发型、身体姿态、手部、下装、"
                            "配饰、构图、机位、透视、光线、色调和画面尺寸不变。禁止生成纯色、白色、"
                            "灰色、透明或摄影棚背景。只允许在服装覆盖区域做必要修改。"
                        ),
                        "aspect_ratio": "9:16",
                        "resolution": "2k",
                        "output_format": "png",
                        "images": [
                            "https://media.example.test/0.png",
                            "https://media.example.test/1.png",
                        ],
                        **(
                            {"quality": "high"}
                            if provider == "gpt"
                            else {
                                "enable_web_search": False,
                                "enable_image_search": False,
                            }
                        ),
                    },
                )
            ]

            repeated = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert repeated.status_code == 201
            assert repeated.json()["id"] == batch_id
            assert len(adapter.submissions) == 1

            fetched = await client.get(
                f"/api/v1/image-generation-batches/{batch_id}",
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert fetched.status_code == 200
            assert fetched.json()["items"][0]["referenceUrls"] == refs
            assert adapter.queries == []
            await _refresh_active_image_generation_batches(app)
            assert adapter.queries == ["prediction-1"]

            hidden = await client.get(
                f"/api/v1/image-generation-batches/{batch_id}",
                headers={"X-User-Id": settings.dev_admin_id},
            )
            assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_user_requirement_reaches_provider_and_survives_history(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    manifest = _manifest()
    requirement = "改为室内背景，图二是服装背面"
    manifest["items"][0]["userRequirement"] = requirement
    data, files = _multipart(manifest)
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "priority-brief"}

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["items"][0]["userRequirement"] == requirement
            assert body["items"][0]["prompts"] == manifest["items"][0]["prompts"]
            prompt = adapter.submissions[0][1]["prompt"]
            assert f"用户补充要求（优先）：\n{requirement}" in prompt
            assert "以用户补充要求为准" in prompt
            fetched = await client.get(
                f"/api/v1/image-generation-batches/{body['id']}", headers=headers
            )
            assert fetched.status_code == 200
            assert fetched.json()["items"][0]["userRequirement"] == requirement


@pytest.mark.asyncio
async def test_production_images_do_not_require_experimental_pilot_allowlist(
    tmp_path: Path,
) -> None:
    user_id = uuid.uuid4()
    app, _settings = await _production_app(
        tmp_path,
        user_id=user_id,
        pilot_user_ids=[],
        pilot_budget_limit=1,
    )
    adapter = FakeWaveSpeedAdapter()
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            logged_in = await client.post(
                "/api/v1/auth/login",
                json={"username": "pilot-user", "password": "pilot-password"},
            )
            assert logged_in.status_code == 200, logged_in.text
            csrf = logged_in.json()["csrfToken"]
            response = await client.post(
                "/api/v1/image-generation-batches",
                headers={"X-CSRF-Token": csrf, "Idempotency-Key": "pilot-denied"},
                data=data,
                files=files,
            )
    assert response.status_code == 201, response.text
    assert len(adapter.submissions) == 1


@pytest.mark.asyncio
async def test_production_images_do_not_consume_pilot_count_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    user_id = uuid.uuid4()
    app, _settings = await _production_app(
        tmp_path,
        user_id=user_id,
        pilot_user_ids=[str(user_id)],
        pilot_budget_limit=1,
    )
    adapter = FakeWaveSpeedAdapter()
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            logged_in = await client.post(
                "/api/v1/auth/login",
                json={"username": "pilot-user", "password": "pilot-password"},
            )
            assert logged_in.status_code == 200, logged_in.text
            csrf = logged_in.json()["csrfToken"]
            request_headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "pilot-accepted"}
            created = await client.post(
                "/api/v1/image-generation-batches",
                headers=request_headers,
                data=data,
                files=files,
            )
            assert created.status_code == 201, created.text

            replay_data, replay_files = _multipart(_manifest())
            replay = await client.post(
                "/api/v1/image-generation-batches",
                headers=request_headers,
                data=replay_data,
                files=replay_files,
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["id"] == created.json()["id"]

            second_data, second_files = _multipart(_manifest(name="second-pilot"))
            second = await client.post(
                "/api/v1/image-generation-batches",
                headers={"X-CSRF-Token": csrf, "Idempotency-Key": "pilot-exhausted"},
                data=second_data,
                files=second_files,
            )
            assert second.status_code == 201, second.text
            assert second.json()["id"] != created.json()["id"]

        async with app.state.database.session_factory() as session:
            current = await session.scalar(
                select(RuntimeConfigVersion).order_by(RuntimeConfigVersion.version.desc()).limit(1)
            )
            assert current is not None
            # Ordinary production batches do not touch the separately managed
            # experimental Pilot counter or its user allow-list.
            assert current.config_json["pilotSubmittedCount"] == 0
        assert len(adapter.submissions) == 2


@pytest.mark.asyncio
async def test_batch_rejects_mismatched_count_and_idempotency_reuse(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "image-key-2"}
    invalid_data, invalid_files = _multipart(_manifest(expected=2))

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.post(
                "/api/v1/image-generation-batches",
                headers=headers,
                data=invalid_data,
                files=invalid_files,
            )
            assert invalid.status_code == 422
            assert "expectedGenerationCount" in invalid.text

            data, files = _multipart(_manifest())
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201
            changed_data, changed_files = _multipart(_manifest(name="different"))
            conflict = await client.post(
                "/api/v1/image-generation-batches",
                headers=headers,
                data=changed_data,
                files=changed_files,
            )
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_unknown_submit_outcome_is_persisted_and_never_retried(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter(submit_error=TimeoutError("socket timed out"))
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "image-key-3"}
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201
            assert created.json()["status"] == "write_uncertain"
            assert created.json()["items"][0]["error"]["code"] == ("WAVESPEED_SUBMISSION_UNCERTAIN")
            repeated = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert repeated.status_code == 201
            assert len(adapter.submissions) == 1


@pytest.mark.asyncio
async def test_unverified_adapter_is_rejected_before_upload_or_submission(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    adapter.availability_snapshot = lambda: SimpleNamespace(
        available=False,
        status="WAVESPEED_CONFIGURED_UNVERIFIED",
        reason="administrator verification is required",
    )
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/image-generation-batches",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "unverified-key",
                },
                data=data,
                files=files,
            )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WAVESPEED_CONFIGURED_UNVERIFIED"
    assert adapter.uploads == []
    assert adapter.submissions == []


@pytest.mark.asyncio
async def test_idempotent_recovery_only_resumes_unclaimed_pending_items(tmp_path: Path) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "recovery-key"}
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201
            assert len(adapter.submissions) == 1

            # A prediction with a durable provider ID is never submitted again.
            repeated = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert repeated.status_code == 201
            assert len(adapter.uploads) == 2
            assert len(adapter.submissions) == 1

            async with app.state.database.session_factory() as session:
                item = await session.scalar(select(ImageGenerationItem))
                assert item is not None
                item.status = "PENDING"
                item.provider_task_id = None
                item.request_json = {"referenceFileIndexes": [0]}
                await session.commit()

            resumed = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert resumed.status_code == 201
            assert len(adapter.submissions) == 2

            async with app.state.database.session_factory() as session:
                item = await session.scalar(select(ImageGenerationItem))
                assert item is not None
                item.status = "SUBMITTING"
                item.provider_task_id = None
                item.request_json = {"referenceFileIndexes": [0]}
                await session.commit()

            uncertain = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert uncertain.status_code == 201
            assert uncertain.json()["status"] == "write_uncertain"
            assert len(adapter.submissions) == 2


@pytest.mark.asyncio
async def test_output_materialization_retries_without_exposing_remote_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()

    async def completed_prediction(task_id: str) -> dict[str, Any]:
        adapter.queries.append(task_id)
        return {
            "data": {
                "id": task_id,
                "status": "completed",
                "outputs": ["https://cdn.example.test/private-signed-output.webp?token=secret"],
            }
        }

    materialize_attempts = 0
    materialized_stream = io.BytesIO()
    Image.new("RGB", (360, 640), "#315f41").save(materialized_stream, format="PNG")
    materialized_bytes = materialized_stream.getvalue()

    async def flaky_materialize(_app: Any, output: Any) -> None:
        nonlocal materialize_attempts
        materialize_attempts += 1
        if materialize_attempts == 1:
            raise OSError("temporary CDN failure")
        local = tmp_path / f"{output.id}.png"
        local.write_bytes(materialized_bytes)
        output.object_key = await _app.state.object_store.put_file(
            local, f"image-generations/{output.id}.png", "image/png"
        )
        output.content_type = "image/png"
        output.size_bytes = len(materialized_bytes)
        output.sha256 = hashlib.sha256(materialized_bytes).hexdigest()

    adapter.get_prediction = completed_prediction  # type: ignore[method-assign]
    monkeypatch.setattr(
        "libtv_agent.services.image_generations._materialize_output", flaky_materialize
    )
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "materialize-key"}
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        app.state.wavespeed_adapter = adapter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            batch_id = created.json()["id"]

            await _refresh_active_image_generation_batches(app)
            first_poll = await client.get(
                f"/api/v1/image-generation-batches/{batch_id}",
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert first_poll.json()["status"] == "running"
            assert first_poll.json()["items"][0]["outputs"] == []
            assert "private-signed-output" not in first_poll.text

            await _refresh_active_image_generation_batches(app)
            second_poll = await client.get(
                f"/api/v1/image-generation-batches/{batch_id}",
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert second_poll.json()["status"] == "completed"
            output = second_poll.json()["items"][0]["outputs"][0]
            assert output["url"].startswith("/api/v1/image-generation-outputs/")
            assert output["qualificationStatus"] == "PENDING"
            assert "private-signed-output" not in second_poll.text
            assert materialize_attempts == 2

            pending_upload = await client.post(
                "/api/v1/uploads",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "unqualified-source-upload",
                    "X-Source-Image-Output-Id": output["id"],
                },
                files={"file": ("candidate.png", materialized_bytes, "image/png")},
            )
            assert pending_upload.status_code == 201, pending_upload.text
            assert pending_upload.json()["sourceImageOutputId"] == output["id"]

            wrong_bytes = await client.post(
                "/api/v1/uploads",
                headers={
                    **headers,
                    "Idempotency-Key": "wrong-source-bytes",
                    "X-Source-Image-Output-Id": output["id"],
                },
                files={"file": ("changed.png", b"changed", "image/png")},
            )
            assert wrong_bytes.status_code == 409
            assert wrong_bytes.json()["detail"]["code"] == "SOURCE_IMAGE_HASH_MISMATCH"

            other_id = uuid.uuid4()
            async with app.state.database.session_factory() as session:
                session.add(
                    User(
                        id=other_id,
                        username="other-source-owner",
                        password_hash="offline-disabled",
                        role=UserRole.OPERATOR,
                    )
                )
                await session.commit()
            foreign = await client.post(
                "/api/v1/uploads",
                headers={
                    "X-User-Id": str(other_id),
                    "Idempotency-Key": "foreign-source",
                    "X-Source-Image-Output-Id": output["id"],
                },
                files={"file": ("candidate.png", materialized_bytes, "image/png")},
            )
            assert foreign.status_code == 404

            reviewed = await client.put(
                f"/api/v1/image-generation-outputs/{output['id']}/quality-decision",
                headers={"X-User-Id": settings.dev_user_id},
                json={
                    "decision": "REJECTED",
                    "productFidelityPass": False,
                    "humanCriticalPass": False,
                    "humanReviewSeconds": 8,
                    "notes": "pilot candidate ground truth",
                },
            )
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["qualificationStatus"] == "REJECTED"
            assert reviewed.json()["qualityGates"]["PRODUCT_FIDELITY"]["outcome"] == "FAIL"

            traced_upload = await client.post(
                "/api/v1/uploads",
                headers={
                    "X-User-Id": settings.dev_user_id,
                    "Idempotency-Key": "legacy-rejected-source-upload",
                    "X-Source-Image-Output-Id": output["id"],
                },
                files={"file": ("candidate.png", materialized_bytes, "image/png")},
            )
            assert traced_upload.status_code == 201, traced_upload.text
            assert traced_upload.json()["sourceImageOutputId"] == output["id"]


@pytest.mark.asyncio
async def test_startup_recovers_active_image_batches_without_browser_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, settings = await _app(tmp_path)
    adapter = FakeWaveSpeedAdapter()
    app.state.wavespeed_adapter = adapter
    headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "startup-recovery"}
    data, files = _multipart(_manifest())

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/image-generation-batches", headers=headers, data=data, files=files
            )
            assert created.status_code == 201
            assert created.json()["status"] == "running"

    async def completed_prediction(task_id: str) -> dict[str, Any]:
        adapter.queries.append(task_id)
        return {
            "data": {
                "id": task_id,
                "status": "completed",
                "outputs": ["https://cdn.example.test/recovered.png"],
            }
        }

    async def materialize(_app: Any, output: Any) -> None:
        output.object_key = f"image-generations/{output.id}.png"
        output.content_type = "image/png"
        output.size_bytes = 10
        output.sha256 = "0" * 64

    adapter.get_prediction = completed_prediction  # type: ignore[method-assign]
    monkeypatch.setattr("libtv_agent.services.image_generations._materialize_output", materialize)

    async with app.router.lifespan_context(app):
        async with app.state.database.session_factory() as session:
            batch = await session.scalar(select(ImageGenerationBatch))
            item = await session.scalar(select(ImageGenerationItem))
            assert batch is not None
            assert item is not None
            assert batch.status == "COMPLETED"
            assert item.status == "COMPLETED"
            assert item.outputs[0].object_key is not None

    assert adapter.queries == ["prediction-1"]
