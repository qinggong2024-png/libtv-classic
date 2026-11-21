"""Current CLI model capabilities must not inherit other models' limits."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.app import create_app
from libtv_agent.domain.models import Base
from libtv_agent.adapters.video_provider import ProviderAvailability

from libtv_agent.adapters.kling_official_cli import (
    KlingCliError,
    KlingOfficialCliAdapter,
    cli_model_limits,
)
from libtv_agent.adapters.video_provider import ProviderCapabilities, VideoGenerationRequest
from libtv_agent.config import Settings


def _capabilities() -> ProviderCapabilities:
    omni = {
        "model": "kling-video-v3_0_omni",
        "arguments": [{"name": "duration", "allowedValues": [str(n) for n in range(3, 16)]}],
        "inputs": [{"name": f"image_{n}"} for n in range(1, 8)],
    }
    v4 = {
        "model": "kling-video-v4_0",
        "arguments": [{"name": "duration", "allowedValues": [str(n) for n in range(3, 31)]}],
        "inputs": [{"name": f"image_{n}"} for n in range(1, 11)],
    }
    return ProviderCapabilities(
        cli_version="kling-cli-test",
        checked_at=datetime.now(UTC),
        authenticated=True,
        account_type=None,
        supported_models=(omni["model"], v4["model"]),
        supported_tools=("omni_ref_video",),
        video_durations=tuple(str(n) for n in range(3, 31)),
        image_input_limit=10,
        raw_capabilities={"availableModels": {"omni_ref_video": {"models": [omni, v4]}}},
    )


def test_selected_omni_model_does_not_inherit_v4_capabilities() -> None:
    capabilities = _capabilities()
    omni = cli_model_limits(capabilities, "kling-video-v3_0_omni")
    v4 = cli_model_limits(capabilities, "kling-video-v4_0")

    assert omni == {
        "imageInputLimit": 7,
        "supportsMultipleImages": True,
        "videoDurations": [str(n) for n in range(3, 16)],
        "tailImageVideoDurations": [str(n) for n in range(3, 16)],
        "supportsElementReferences": False,
        "supportsProductReferences": True,
    }
    assert v4 is not None
    assert v4["imageInputLimit"] == 10
    assert v4["videoDurations"][-1] == "30"
    assert cli_model_limits(capabilities, "missing-model") is None


@pytest.mark.parametrize("storyboards,products", [(1, 1), (3, 1), (3, 2), (4, 3)])
@pytest.mark.parametrize("duration", [5, 10, 15])
def test_cli_image_matrix_within_selected_omni_limits(
    tmp_path: Path, storyboards: int, products: int, duration: int,
) -> None:
    settings = Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path)
    adapter = KlingOfficialCliAdapter(settings)
    capabilities = _capabilities()
    request = VideoGenerationRequest(
        prompt="Test product references",
        model="kling-video-v3_0_omni",
        image_paths=tuple(Path(f"shot-{n}.png") for n in range(storyboards)),
        product_reference_paths=tuple(Path(f"product-{n}.png") for n in range(products)),
        duration=duration,
        aspect_ratio="9:16",
    )
    assert adapter._select_model(capabilities, request.model, request) == "kling-video-v3_0_omni"


@pytest.mark.parametrize("storyboards,products,duration", [(5, 3, 10), (3, 1, 16)])
def test_cli_rejects_out_of_model_bounds_before_paid_submit(
    tmp_path: Path, storyboards: int, products: int, duration: int,
) -> None:
    settings = Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path)
    adapter = KlingOfficialCliAdapter(settings)
    request = VideoGenerationRequest(
        prompt="Test product references",
        model="kling-video-v3_0_omni",
        image_paths=tuple(Path(f"shot-{n}.png") for n in range(storyboards)),
        product_reference_paths=tuple(Path(f"product-{n}.png") for n in range(products)),
        duration=duration,
        aspect_ratio="9:16",
    )
    with pytest.raises(KlingCliError) as exc:
        adapter._select_model(_capabilities(), request.model, request)
    assert exc.value.code in {"KLING_IMAGE_INPUT_LIMIT_EXCEEDED", "KLING_CAPABILITY_UNSUPPORTED"}


@pytest.mark.asyncio
async def test_system_status_uses_selected_cli_model_not_global_model_union(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        kling_provider_backend="cli",
        kling_cli_enabled=True,
        kling_cli_default_model="kling-video-v3_0_omni",
        runner_root=tmp_path / "runner",
        artifact_root=tmp_path / "artifacts",
        _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        app.state.kling_adapter._capabilities = _capabilities()
        app.state.kling_adapter._last_availability = ProviderAvailability(
            status="KLING_CLI_READY", available=True, authenticated=True,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/system/status")
    assert response.status_code == 200, response.text
    model = response.json()["videoGeneration"]
    assert model["available"] is True
    assert model["model"] == "kling-video-v3_0_omni"
    assert model["imageInputLimit"] == 7
    assert model["videoDurations"][-1] == "15"
    assert model["tailImageVideoDurations"][-1] == "15"
    assert model["supportsElementReferences"] is False
    assert model["supportsProductReferences"] is True
