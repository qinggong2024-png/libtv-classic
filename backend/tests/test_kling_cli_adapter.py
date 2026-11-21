import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
    map_kling_status,
    parse_cli_json,
)
from libtv_agent.adapters.video_provider import ProviderOutput, VideoGenerationRequest
from libtv_agent.config import Settings


def _envelope(body: dict[str, Any]) -> str:
    return json.dumps({"ok": True, "status": 200, "body": body})


def _identity(*, tail: bool = True) -> dict[str, Any]:
    inputs: list[dict[str, object]] = [{"name": "input", "required": True}]
    if tail:
        inputs.append({"name": "tail_image", "required": False})
    return {
        "user": {"userId": 123},
        "availableModels": {
            "image_to_video": {
                "models": [
                    {
                        "model": "runtime-video-model",
                        "arguments": [
                            {"name": "prompt", "required": True},
                            {"name": "duration", "allowedValues": ["5", "10"]},
                            {"name": "aspect_ratio", "allowedValues": ["9:16", "16:9"]},
                        ],
                        "inputs": inputs,
                    }
                ]
            }
        },
        "authMode": "oauth",
    }


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "kling_cli_enabled": True,
        "kling_cli_global_qps": 20,
        "kling_cli_capability_cache_seconds": 3600,
        "runner_root": tmp_path / "runner",
        **updates,
    }
    return Settings(_env_file=None, **values)


def _runner(
    calls: list[list[str]],
    *,
    identity: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
):
    identity_body = identity or _identity()
    query_body = query or {
        "generationId": "generation-123",
        "status": "RUNNING",
        "works": [],
    }

    async def run(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        assert deadline_seconds > 0
        calls.append(arguments)
        if arguments[0] == "--version":
            return CliProcessResult(0, "0.1.3\n", "")
        command = arguments[5]
        body: dict[str, Any]
        if command == "who_am_i":
            body = identity_body
        elif command == "tool_list":
            body = {
                "tools": [
                    {"name": name, "description": "", "inputSchema": {}}
                    for name in (
                        "who_am_i",
                        "image_to_video",
                        "query_tasks",
                        "file_upload",
                    )
                ]
            }
        elif command == "image_to_video":
            body = {
                "generationId": "generation-123",
                "status": "submitted",
                "creditsConsumed": 15,
            }
        elif command == "query_tasks":
            body = query_body
        elif command == "file_upload":
            body = {"url": f"https://uploads.invalid/{Path(arguments[6]).name}"}
        elif command == "account":
            body = {"membershipType": "VIP", "availableRemainCredits": 100}
        elif command in {"login", "logout"}:
            body = {"authenticated": command == "login"}
        else:
            return CliProcessResult(2, "", "unknown command")
        return CliProcessResult(0, _envelope(body), "")

    return run




@pytest.mark.asyncio
async def test_cli_credit_balance_is_read_only_and_explicit_insufficient_is_not_unknown(tmp_path):
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner(calls))
    assert await adapter.account_credits() == 100
    assert calls[-1][5] == "account"
    assert not any("omni_ref_video" in item or "image_to_video" in item for item in calls)

    async def reject(arguments, _timeout):
        return CliProcessResult(2, "", "Insufficient credits to create this task")

    rejecting = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=reject)
    with pytest.raises(KlingCliError) as error:
        await rejecting._invoke("omni_ref_video", submitted=True)
    assert error.value.code == "KLING_INSUFFICIENT_CREDITS"
    assert error.value.submitted is False
    assert error.value.uncertain is False

def test_parse_cli_json_accepts_last_json_line_and_rejects_invalid() -> None:
    assert parse_cli_json('notice\n{"ok":true,"body":{"value":1}}')["ok"] is True
    with pytest.raises(KlingCliError, match="invalid JSON"):
        parse_cli_json("not json")


@pytest.mark.asyncio
async def test_omni_cli_preserves_storyboard_and_independent_product_images(
    tmp_path: Path,
) -> None:
    identity = {
        "user": {"accountType": "VIP"},
        "availableModels": {
            "omni_ref_video": {
                "models": [{
                    "model": "kling-video-v3_0_omni",
                    "arguments": [
                        {"name": "duration", "allowedValues": ["5", "10", "15"]},
                        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
                    ],
                    "inputs": [{"name": f"image_{i}"} for i in range(1, 8)],
                }],
            },
        },
    }
    calls: list[list[str]] = []
    mocked = _runner(calls, identity=identity)

    async def run(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        if "omni_ref_video" in arguments:
            calls.append(arguments)
            return CliProcessResult(0, _envelope({
                "generationId": "omni-id-001", "status": "submitted",
            }), "")
        return await mocked(arguments, deadline_seconds)

    scene = tmp_path / "scene.png"
    product = tmp_path / "product.png"
    scene.write_bytes(b"scene")
    product.write_bytes(b"product")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=run)
    result = await adapter.submit(VideoGenerationRequest(
        prompt="Follow source image, preserve shirt print",
        model="kling-video-v3_0_omni",
        image_paths=(scene,),
        product_reference_paths=(product,),
        duration=10,
        aspect_ratio="9:16",
    ))
    assert result.provider_task_id == "omni-id-001"
    submission = next(call for call in calls if "omni_ref_video" in call)
    assert submission.count("--image") == 2
    assert submission.index("https://uploads.invalid/scene.png") < submission.index(
        "https://uploads.invalid/product.png"
    )
    assert "--model" in submission
    assert "kling-video-v3_0_omni" in submission


@pytest.mark.parametrize(
    ("prompt", "multi_shot", "scene_count", "expected_prompt"),
    [
        (
            "References: @image_1 actor; @image_2 garment.",
            True,
            1,
            "References: 图片1 actor; 图片2 garment.",
        ),
        (
            "Use @image_1 for wearer; @image_2 for product. CUT TO a close-up.",
            False,
            1,
            "Use 图片1 for wearer; 图片2 for product. CUT TO a close-up.",
        ),
        (
            "@image_1 wearer with @image_2 product, a natural surprised reaction.",
            True,
            1,
            "图片1 wearer with 图片2 product, a natural surprised reaction.",
        ),
        (
            "From @image_1 to @image_2, garment is @image_3.",
            True,
            2,
            "From 图片1 to 图片2, garment is 图片3.",
        ),
        ("Unbound @image_4.", True, 1, None),
        ("Unbound @image_0.", False, 1, None),
    ],
)
@pytest.mark.asyncio
async def test_omni_cli_image_refs_follow_bound_slots_regardless_of_cut_words(
    tmp_path: Path,
    prompt: str,
    multi_shot: bool,
    scene_count: int,
    expected_prompt: str | None,
) -> None:
    identity = {
        "user": {"accountType": "VIP"},
        "availableModels": {
            "omni_ref_video": {
                "models": [{
                    "model": "kling-video-v3_0_omni",
                    "arguments": [
                        {"name": "duration", "allowedValues": ["10"]},
                        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
                        {"name": "prefer_multi_shots", "allowedValues": ["true", "false"]},
                    ],
                    "inputs": [{"name": f"image_{i}"} for i in range(1, 4)],
                }],
            },
        },
    }
    calls: list[list[str]] = []
    fallback = _runner(calls, identity=identity)

    async def run(arguments: list[str], deadline: float) -> CliProcessResult:
        if "omni_ref_video" in arguments:
            calls.append(arguments)
            return CliProcessResult(0, _envelope({
                "generationId": "mock-no-charge",
                "status": "submitted",
            }), "")
        return await fallback(arguments, deadline)

    scenes = tuple(tmp_path / f"scene-{i}.png" for i in range(1, scene_count + 1))
    product = tmp_path / "garment.png"
    for path in (*scenes, product):
        path.write_bytes(b"offline-fixture")
    request = VideoGenerationRequest(
        prompt=prompt,
        model="kling-video-v3_0_omni",
        image_paths=scenes,
        product_reference_paths=(product,),
        duration=10,
        aspect_ratio="9:16",
        multi_shot=multi_shot,
    )
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=run)

    if expected_prompt is None:
        with pytest.raises(KlingCliError) as error:
            await adapter.submit(request)
        assert error.value.code == "KLING_IMAGE_REFERENCE_UNAVAILABLE"
        assert not any("omni_ref_video" in call for call in calls)
        return

    result = await adapter.submit(request)
    assert result.provider_task_id == "mock-no-charge"
    submission = next(call for call in calls if "omni_ref_video" in call)
    assert submission[submission.index("--prefer_multi_shots") + 1] == str(multi_shot).lower()
    assert [
        submission[i + 1] for i, value in enumerate(submission[:-1]) if value == "--image"
    ] == [f"https://uploads.invalid/{path.name}" for path in (*scenes, product)]
    assert submission[-1].endswith(expected_prompt)
    assert "@image_" not in submission[-1]


@pytest.mark.asyncio
async def test_non_omni_cli_keeps_existing_reference_text(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner(calls))
    scene = tmp_path / "scene.png"
    scene.write_bytes(b"offline-fixture")
    prompt = "This model keeps @image_1 unchanged."
    await adapter.submit(VideoGenerationRequest(
        prompt=prompt,
        model="runtime-video-model",
        image_paths=(scene,),
        duration=10,
        aspect_ratio="9:16",
    ))
    submission = next(call for call in calls if "image_to_video" in call)
    assert submission[-1] == prompt


@pytest.mark.asyncio
async def test_three_omni_variants_keep_distinct_trace_ids_and_image_roles(
    tmp_path: Path,
) -> None:
    identity = {
        "user": {"accountType": "VIP"},
        "availableModels": {
            "omni_ref_video": {
                "models": [{
                    "model": "kling-video-v3_0_omni",
                    "arguments": [
                        {"name": "duration", "allowedValues": ["10"]},
                        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
                    ],
                    "inputs": [{"name": f"image_{i}"} for i in range(1, 8)],
                }],
            },
        },
    }
    submitted: list[list[str]] = []
    default_runner = _runner([], identity=identity)

    async def runner(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        if "omni_ref_video" not in arguments:
            return await default_runner(arguments, deadline_seconds)
        submitted.append(arguments)
        await asyncio.sleep(0.005)
        trace = arguments[arguments.index("--task-trace-id") + 1]
        return CliProcessResult(0, _envelope({
            "generationId": f"remote-{trace}",
            "status": "QUEUING",
            "creditsConsumed": 12,
        }), "")

    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path, kling_cli_max_submit_concurrency=2),
        process_runner=runner,
    )
    requests = []
    for index in range(3):
        storyboard = tmp_path / f"scene-{index}.png"
        product = tmp_path / f"product-{index}.png"
        storyboard.write_bytes(f"scene-{index}".encode())
        product.write_bytes(f"product-{index}".encode())
        requests.append(VideoGenerationRequest(
            prompt=f"Variant {index}: @image_1 scene, @image_2 product",
            model="kling-video-v3_0_omni",
            image_paths=(storyboard,),
            product_reference_paths=(product,),
            duration=10,
            aspect_ratio="9:16",
            task_trace_id=f"attempt-{index}",
        ))
    results = await asyncio.gather(*(adapter.submit(request) for request in requests))
    assert {result.provider_task_id for result in results} == {
        f"remote-attempt-{index}" for index in range(3)
    }
    assert all(result.credits_consumed == 12 for result in results)
    assert len(submitted) == 3
    for argv in submitted:
        trace = argv[argv.index("--task-trace-id") + 1]
        index = int(trace.rsplit("-", 1)[1])
        actual_images = [
            argv[pos + 1] for pos, value in enumerate(argv[:-1])
            if value == "--image"
        ]
        assert actual_images == [
            f"https://uploads.invalid/{requests[index].image_paths[0].name}",
            f"https://uploads.invalid/{requests[index].product_reference_paths[0].name}",
        ]
        expected_prompt = requests[index].prompt.replace("@image_1", "图片1").replace(
            "@image_2", "图片2"
        )
        assert argv[-1].endswith(expected_prompt)
        assert "@image_" not in argv[-1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("QUEUING", "queued"),
        ("processing", "running"),
        ("COMPLETED", "succeeded"),
        ("PARTIAL_COMPLETED", "partial"),
        ("FAILED", "failed"),
        ("NEW_REMOTE_STATE", "unknown"),
    ],
)
def test_status_mapping(raw: str, expected: str) -> None:
    assert map_kling_status(raw) == expected


@pytest.mark.asyncio
async def test_not_installed_and_not_authenticated_are_explicit(tmp_path: Path) -> None:
    async def missing(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        del arguments, deadline_seconds
        raise KlingCliError("KLING_CLI_NOT_INSTALLED", "missing")

    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=missing)
    assert (await adapter.check_availability()).status == "KLING_CLI_NOT_INSTALLED"

    async def logged_out(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        del deadline_seconds
        if "--version" in arguments:
            return CliProcessResult(0, "0.1.3", "")
        return CliProcessResult(1, "", "Login required")

    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=logged_out)
    assert (await adapter.check_availability()).status == "KLING_CLI_NOT_AUTHENTICATED"


@pytest.mark.asyncio
async def test_login_and_logout_use_official_cli_and_update_status(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner(calls))

    assert (await adapter.login()).status == "KLING_CLI_READY"
    assert any(len(call) > 5 and call[5] == "login" for call in calls)

    logged_out = await adapter.logout()
    assert logged_out.status == "KLING_CLI_NOT_AUTHENTICATED"
    assert logged_out.authenticated is False
    assert any(len(call) > 5 and call[5] == "logout" for call in calls)


@pytest.mark.asyncio
async def test_submit_uses_argv_and_preserves_special_prompt(tmp_path: Path) -> None:
    image = tmp_path / "含 空格.png"
    image.write_bytes(b"image")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner(calls))
    prompt = '中文 "quoted"; $(should-not-run) & more'
    result = await adapter.submit(
        VideoGenerationRequest(
            prompt=prompt,
            model=None,
            image_paths=(image,),
            duration=10,
            aspect_ratio="9:16",
            task_trace_id="a" * 32,
        )
    )
    assert result.provider_task_id == "generation-123"
    submission = next(call for call in calls if "image_to_video" in call)
    assert prompt in submission
    assert submission.count(prompt) == 1
    assert f"https://uploads.invalid/{image.name}" in submission
    assert result.provider_model == "runtime-video-model"


@pytest.mark.asyncio
async def test_valid_stdout_wins_when_stderr_contains_warning(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    base_runner = _runner(calls)

    async def warning_runner(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        result = await base_runner(arguments, deadline_seconds)
        if "image_to_video" in arguments:
            return CliProcessResult(result.exit_code, result.stdout, "warning: harmless")
        return result

    image = tmp_path / "one.png"
    image.write_bytes(b"image")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=warning_runner)
    result = await adapter.submit(
        VideoGenerationRequest(
            prompt="animate",
            model=None,
            image_paths=(image,),
            duration=10,
            aspect_ratio="9:16",
        )
    )
    assert result.provider_task_id == "generation-123"


@pytest.mark.asyncio
async def test_multi_storyboard_is_not_silently_dropped(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner([], identity=_identity(tail=False))
    )
    with pytest.raises(KlingCliError) as captured:
        await adapter.validate_request(
            VideoGenerationRequest(
                prompt="animate",
                model=None,
                image_paths=(first, second),
                duration=10,
                aspect_ratio="9:16",
            )
        )
    assert captured.value.code == "KLING_MULTI_STORYBOARD_UNSUPPORTED"


@pytest.mark.asyncio
async def test_numbered_multi_image_model_passes_every_storyboard(tmp_path: Path) -> None:
    identity = _identity(tail=False)
    model = identity["availableModels"]["image_to_video"]["models"][0]
    model["inputs"] = [{"name": f"image_{index}", "required": index == 1} for index in range(1, 4)]
    paths = tuple(tmp_path / f"{index}.png" for index in range(3))
    for index, path in enumerate(paths):
        path.write_bytes(str(index).encode())
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner(calls, identity=identity)
    )
    await adapter.submit(
        VideoGenerationRequest(
            prompt="依次展示图片1、图片2、图片3",
            model=None,
            image_paths=paths,
            duration=10,
            aspect_ratio="9:16",
        )
    )
    submission = next(call for call in calls if "image_to_video" in call)
    assert submission.count("--image") == 3
    assert "--tailImage" not in submission
    assert all(f"https://uploads.invalid/{path.name}" in submission for path in paths)


@pytest.mark.asyncio
async def test_product_references_follow_storyboards_and_receive_role_guard(
    tmp_path: Path,
) -> None:
    identity = _identity(tail=False)
    model = identity["availableModels"]["image_to_video"]["models"][0]
    model["inputs"] = [{"name": f"image_{index}", "required": index == 1} for index in range(1, 4)]
    storyboard_paths = (tmp_path / "storyboard-1.png", tmp_path / "storyboard-2.png")
    product_path = tmp_path / "product-reference-1.png"
    for path in (*storyboard_paths, product_path):
        path.write_bytes(b"image")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner(calls, identity=identity)
    )

    await adapter.submit(
        VideoGenerationRequest(
            prompt="模特自然转身展示服装",
            model=None,
            image_paths=storyboard_paths,
            product_reference_paths=(product_path,),
            duration=10,
            aspect_ratio="9:16",
        )
    )

    submission = next(call for call in calls if "image_to_video" in call)
    image_values = [
        submission[index + 1] for index, value in enumerate(submission[:-1]) if value == "--image"
    ]
    assert image_values == [
        f"https://uploads.invalid/{path.name}" for path in (*storyboard_paths, product_path)
    ]
    prompt = submission[-1]
    assert "图片1至图片2是分镜图" in prompt
    assert "图片3是主产品参考图" in prompt
    assert "图片3只用于参考商品外观、款式、印花、Logo与可见文字" in prompt
    assert "不得采用产品参考图的人物、背景、构图、摆放方式或拍摄姿势" in prompt
    assert prompt.endswith("模特自然转身展示服装")


@pytest.mark.asyncio
async def test_existing_reference_role_prompt_is_not_duplicated(tmp_path: Path) -> None:
    identity = _identity(tail=False)
    model = identity["availableModels"]["image_to_video"]["models"][0]
    model["inputs"] = [{"name": f"image_{index}", "required": index == 1} for index in range(1, 3)]
    storyboard = tmp_path / "storyboard.png"
    product = tmp_path / "product.png"
    storyboard.write_bytes(b"storyboard")
    product.write_bytes(b"product")
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner(calls, identity=identity)
    )
    prompt = (
        "References: @image_1 are ordered storyboards controlling scene, action, "
        "composition, and shot order. @image_2 is the primary product reference. "
        "动作：人物自然向前走两到三步后停稳。"
    )

    await adapter.submit(
        VideoGenerationRequest(
            prompt=prompt,
            model=None,
            image_paths=(storyboard,),
            product_reference_paths=(product,),
            duration=10,
            aspect_ratio="9:16",
        )
    )

    submission = next(call for call in calls if "image_to_video" in call)
    assert submission[-1] == prompt
    assert "【输入图片用途，必须严格遵守】" not in submission[-1]
    assert "References:" in submission[-1]


@pytest.mark.asyncio
async def test_product_reference_requires_numbered_multi_image_model(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.png"
    product = tmp_path / "product.png"
    storyboard.write_bytes(b"storyboard")
    product.write_bytes(b"product")
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner([], identity=_identity(tail=True))
    )

    with pytest.raises(KlingCliError) as captured:
        await adapter.validate_request(
            VideoGenerationRequest(
                prompt="animate",
                model=None,
                image_paths=(storyboard,),
                product_reference_paths=(product,),
                duration=10,
                aspect_ratio="9:16",
            )
        )

    assert captured.value.code == "KLING_PRODUCT_REFERENCE_UNSUPPORTED"


@pytest.mark.asyncio
async def test_storyboards_and_product_references_share_model_image_limit(tmp_path: Path) -> None:
    identity = _identity(tail=False)
    model = identity["availableModels"]["image_to_video"]["models"][0]
    model["inputs"] = [{"name": f"image_{index}", "required": index == 1} for index in range(1, 4)]
    paths = tuple(tmp_path / f"{index}.png" for index in range(4))
    for path in paths:
        path.write_bytes(b"image")
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path), process_runner=_runner([], identity=identity)
    )

    with pytest.raises(KlingCliError) as captured:
        await adapter.validate_request(
            VideoGenerationRequest(
                prompt="animate",
                model=None,
                image_paths=paths[:2],
                product_reference_paths=paths[2:],
                duration=10,
                aspect_ratio="9:16",
            )
        )

    assert captured.value.code == "KLING_IMAGE_INPUT_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_completed_prefers_watermark_free_video_url(tmp_path: Path) -> None:
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path),
        process_runner=_runner(
            [],
            query={
                "generationId": "generation-123",
                "status": "COMPLETED",
                "works": [{
                    "url": "https://cdn.example/watermarked.mp4",
                    "urlWithoutWatermark": "https://cdn.example/clean.mp4",
                    "contentType": "video/mp4",
                }],
            },
        ),
    )
    result = await adapter.query("generation-123")
    assert result.status == "succeeded"
    assert result.outputs[0].url == "https://cdn.example/clean.mp4"
    assert result.outputs[0].url_without_watermark == "https://cdn.example/clean.mp4"


@pytest.mark.asyncio
async def test_download_uses_watermark_free_url_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            del size
            if getattr(self, "_read", False):
                return b""
            self._read = True
            return b"clean-video"

    urls: list[str] = []

    def mock_urlopen(request: Any, timeout: float) -> Response:
        assert timeout > 0
        urls.append(request.full_url)
        return Response()

    monkeypatch.setattr("libtv_agent.adapters.kling_official_cli.urlopen", mock_urlopen)
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner([]))
    output = ProviderOutput(
        url="https://cdn.example/watermarked.mp4",
        url_without_watermark="https://cdn.example/clean.mp4",
    )
    await adapter.download(output, tmp_path / "clean.mp4")
    assert urls == ["https://cdn.example/clean.mp4"]


@pytest.mark.asyncio
async def test_completed_without_url_remains_pollable_without_resubmission(
    tmp_path: Path,
) -> None:
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path),
        process_runner=_runner(
            [],
            query={
                "generationId": "generation-123",
                "status": "COMPLETED",
                "works": [],
            },
        ),
    )
    result = await adapter.query("generation-123")
    assert result.status == "running"
    assert result.raw_status == "COMPLETED"
    assert not result.outputs


@pytest.mark.asyncio
async def test_download_is_atomic_and_reuses_valid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            del size
            if getattr(self, "_read", False):
                return b""
            self._read = True
            return b"video-bytes"

    monkeypatch.setattr(
        "libtv_agent.adapters.kling_official_cli.urlopen",
        lambda request, timeout: Response(),
    )
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=_runner([]))
    destination = tmp_path / "output.mp4"
    downloaded = await adapter.download(
        ProviderOutput(url="https://cdn.example/output.mp4"), destination
    )
    assert downloaded.path == destination
    assert destination.read_bytes() == b"video-bytes"
    assert not destination.with_suffix(".mp4.part").exists()
    assert (await adapter.download(ProviderOutput(url="unused"), destination)).reused


@pytest.mark.asyncio
async def test_provider_rate_limit_is_shared_across_calls(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    adapter = KlingOfficialCliAdapter(
        _settings(tmp_path, kling_cli_global_qps=20),
        process_runner=_runner(calls),
    )
    started = time.monotonic()
    await adapter.check_availability()
    elapsed = time.monotonic() - started
    assert elapsed >= 0.045


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.asyncio
async def test_confirmed_http_429_is_retried_once_without_duplicate_paid_task(
    tmp_path: Path, exit_code: int,
) -> None:
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    submits = 0

    async def rejecting_runner(arguments: list[str], deadline: float) -> CliProcessResult:
        nonlocal submits
        if "image_to_video" in arguments:
            submits += 1
            if submits == 1:
                calls.append(arguments)
                return CliProcessResult(
                    exit_code,
                    json.dumps({"ok": False, "status": 429,
                                "body": {"message": "Too many requests"}}),
                    "",
                )
        return await base_runner(arguments, deadline)

    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=rejecting_runner)
    result = await adapter.submit(VideoGenerationRequest(
        prompt="animate", model=None, image_paths=(image,), duration=10, aspect_ratio="9:16",
    ))
    assert result.provider_task_id == "generation-123"
    assert submits == 2


@pytest.mark.parametrize("failed_body", [
    "",
    json.dumps({"ok": False, "status": 429,
                "body": {"generationId": "already-accepted", "message": "rate limit"}}),
])
@pytest.mark.asyncio
async def test_unverified_limit_never_resubmits_paid_video(
    tmp_path: Path, failed_body: str,
) -> None:
    calls: list[list[str]] = []
    base_runner = _runner(calls)
    submits = 0

    async def ambiguous_runner(arguments: list[str], deadline: float) -> CliProcessResult:
        nonlocal submits
        if "image_to_video" in arguments:
            submits += 1
            return CliProcessResult(1, failed_body, "rate limit")
        return await base_runner(arguments, deadline)

    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=ambiguous_runner)
    with pytest.raises(KlingCliError) as error:
        await adapter.submit(VideoGenerationRequest(
            prompt="animate", model=None, image_paths=(image,), duration=10, aspect_ratio="9:16",
        ))
    assert error.value.code == "KLING_RATE_LIMITED"
    assert error.value.submitted and error.value.uncertain
    assert submits == 1


@pytest.mark.asyncio
async def test_other_cli_errors_after_submit_remain_uncertain(tmp_path: Path) -> None:
    base_runner = _runner([])
    async def broken(arguments: list[str], deadline: float) -> CliProcessResult:
        if "image_to_video" in arguments:
            return CliProcessResult(0, json.dumps({
                "ok": False, "status": 502, "body": {"message": "upstream error"},
            }), "")
        return await base_runner(arguments, deadline)

    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=broken)
    with pytest.raises(KlingCliError) as error:
        await adapter.submit(VideoGenerationRequest(
            prompt="animate", model=None, image_paths=(image,), duration=10, aspect_ratio="9:16",
        ))
    assert error.value.submitted and error.value.uncertain


@pytest.mark.asyncio
async def test_process_error_does_not_expose_stderr_credentials(tmp_path: Path) -> None:
    secret = "ACCESS_TOKEN=should-never-appear"

    async def failure(arguments: list[str], deadline_seconds: float) -> CliProcessResult:
        del deadline_seconds
        if "--version" in arguments:
            return CliProcessResult(0, "0.1.3", "")
        return CliProcessResult(1, "", secret)

    adapter = KlingOfficialCliAdapter(_settings(tmp_path), process_runner=failure)
    availability = await adapter.check_availability()
    assert secret not in (availability.reason or "")
