"""Offline OOTD routing and wire-contract tests; no model or CLI process is run."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from libtv_agent.adapters.kling_official_cli import (
    CliProcessResult,
    KlingCliError,
    KlingOfficialCliAdapter,
)
from libtv_agent.adapters.video_provider import ProviderAvailability, ProviderCapabilities
from libtv_agent.config import Settings
from libtv_agent.domain.enums import AttemptStatus, UserRole
from libtv_agent.domain.models import (
    Base,
    GenerationAttempt,
    GenerationCommand,
    ParentTask,
    StagedUpload,
    User,
)
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services import analysis, cutflow_site, fixed_video_tasks, ootd_simple
from libtv_agent.services.ai_creative_planner import CompiledCreativeVariant, CreativePlannerError
from libtv_agent.services.kling_preparation import prepare_kling_task
from libtv_agent.templates.site_style_profiles import get_site_style_profile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

MODEL = "kling-video-v3_0_omni"
PROMPT = (
    "从首帧原有半身构图开始，自然整理衣袖，保持原有图案、颜色、人物和环境。"
    "直接切镜到衣领细节，短暂停留后轻轻侧身，保持正常动作速度与真实手持质感。"
    "无音频、无字幕；按用户要求不转身。@image_1 是首帧，@image_2 是商品。"
)


def _variant():
    return CompiledCreativeVariant(
        prompt=PROMPT, creative_idea="natural outfit display", visual_hook="first frame",
        buyer_reason="show garment", beats=(),
        facts=({"person_count_verified": False},), planner_version=ootd_simple.PROFILE,
        request_attempts=1,
    )


@pytest.fixture
async def ootd_engine():
    # This suite changes no schema or persistence implementation. These are isolated
    # task/asset fixtures; migration fault injection remains in verify_classic_chain.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def harness(tmp_path, monkeypatch, ootd_engine):
    factory = async_sessionmaker(ootd_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(id=uuid.uuid4(), username="ootd-test", password_hash="unused",
                     role=UserRole.USER)
        session.add(owner)
        await session.flush()
        uploads = []
        for index, name in enumerate(("first", "product", "extra", "product-back"), 1):
            uploads.append(StagedUpload(
                id=uuid.uuid4(), user_id=owner.id, original_filename=f"{name}.png",
                sha256=f"{index:064d}", mime_type="image/png", size_bytes=1000,
                width=1080, height=1920, object_key=f"{name}.png",
                thumbnail_key=f"{name}-thumb.png",
            ))
        session.add_all(uploads)
        await session.commit()
        settings = Settings(
            app_env="test", ai_ootd_v1_enabled=True, ai_creative_v25_enabled=True,
            ai_adapter_mode="openai", openai_model="gpt-6.1-sol",
            openai_api_key="offline-only", openai_base_url="https://offline.invalid/v1",
            kling_provider_backend="cli", kling_cli_enabled=True, kling_cli_model=MODEL,
            # Explicitly test that the task's off overrides an enabled global default.
            kling_api_audio="native", artifact_root=tmp_path / "artifacts",
            runner_root=tmp_path / "runner", kling_cli_global_qps=20, _env_file=None,
        )
        planner = AsyncMock(return_value=(_variant(),))
        monkeypatch.setattr(ootd_simple, "plan_simple_ootd", planner)
        guards = []
        for name in ("plan_creative_video", "observe_storyboards", "motion_action_library"):
            guard = AsyncMock(side_effect=AssertionError(f"legacy path called: {name}"))
            monkeypatch.setattr(fixed_video_tasks, name, guard)
            guards.append(guard)
        compiler = Mock(side_effect=AssertionError("old prompt compiler called"))
        monkeypatch.setattr(fixed_video_tasks, "compile_video_plan", compiler)
        monkeypatch.setattr(fixed_video_tasks, "get_merged_site_style_profile",
                            AsyncMock(return_value=get_site_style_profile("pagewings")))
        monkeypatch.setattr(cutflow_site, "creation_snapshot", AsyncMock(return_value=None))
        monkeypatch.setattr(analysis, "_analysis_image_url", AsyncMock(
            side_effect=lambda store, key, *args: f"data:image/png;fixture,{key}",
        ))

        async def queued(session, *, task, **kwargs):
            return task

        queue = AsyncMock(side_effect=queued)
        monkeypatch.setattr(fixed_video_tasks, "queue_video_task", queue)

        async def localize(key, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(key.encode())
            return destination

        store = SimpleNamespace(localize=AsyncMock(side_effect=localize))
        adapter = SimpleNamespace(
            get_provider_name=lambda: settings.kling_cli_provider_name,
            check_availability=AsyncMock(return_value=ProviderAvailability("ready", True, True)),
            validate_request=AsyncMock(return_value=MODEL),
            submit=AsyncMock(side_effect=AssertionError("paid boundary called")),
        )

        def payload(**changes):
            return VideoTaskCreate.model_validate({
                "clientTaskId": "ootd-offline", "title": "OOTD offline",
                "storyboards": [{"uploadId": str(uploads[0].id), "order": 1}],
                "subjectImages": [{"uploadId": str(uploads[1].id), "order": 1}],
                "siteProfileId": "pagewings", "creativeBrief": "不要转身。",
                "paidSubmissionConsent": True, **changes,
            })

        async def create(value=None):
            return await fixed_video_tasks.create_fixed_video_task(
                session, user_id=owner.id, payload=value or payload(), settings=settings,
                kling_adapter=adapter, object_store=store,
            )

        yield SimpleNamespace(
            session=session, settings=settings, owner=owner, uploads=uploads,
            planner=planner, guards=guards, compiler=compiler, queue=queue,
            store=store, adapter=adapter, payload=payload, create=create,
        )
        for guard in guards:
            guard.assert_not_called()
        compiler.assert_not_called()
        adapter.submit.assert_not_called()


def _cli(settings, monkeypatch, *, submission=None, missing_argument=None):
    specs = [
        {"name": "duration", "allowedValues": [str(i) for i in range(5, 16)]},
        {"name": "aspect_ratio", "allowedValues": ["9:16"]},
        {"name": "prefer_multi_shots", "allowedValues": ["true", "false"]},
        {"name": "enable_audio", "allowedValues": ["true", "false"]},
    ]
    specs = [spec for spec in specs if spec["name"] != missing_argument]
    capabilities = ProviderCapabilities(
        cli_version="offline-fixture", checked_at=datetime.now(UTC), authenticated=True,
        account_type="fixture", supported_models=(MODEL,), supported_tools=("omni_ref_video",),
        supports_multiple_images=True, image_input_limit=7,
        raw_capabilities={"availableModels": {"omni_ref_video": {"models": [{
            "model": MODEL, "arguments": specs,
            "inputs": [{"name": f"image_{i}"} for i in range(1, 8)],
        }]}}},
    )
    calls = []

    async def process(arguments, deadline_seconds):
        if "file_upload" in arguments:
            source = Path(arguments[arguments.index("file_upload") + 1])
            return CliProcessResult(0, json.dumps({
                "ok": True, "body": {"url": f"https://uploads.invalid/{source.name}"},
            }), "")
        # The adapter's only executor is this fake. Fail any discovery or API route.
        assert "omni_ref_video" in arguments, arguments
        calls.append(arguments)
        body = submission if submission is not None else {"generationId": "offline-id"}
        return CliProcessResult(0, json.dumps({"ok": True, "body": body}), "")

    adapter = KlingOfficialCliAdapter(settings, process_runner=process)
    monkeypatch.setattr(adapter, "refresh_capabilities", AsyncMock(return_value=capabilities))
    return adapter, calls


@pytest.mark.parametrize("duration", [5, 10, 15])
async def test_default_automatic_ootd_preserves_sol_prompt_and_actual_cli_argv(
    harness, monkeypatch, duration,
):
    h = harness
    task = await h.create(h.payload(durationSeconds=duration))
    h.planner.assert_awaited_once()
    kwargs = h.planner.call_args.kwargs
    assert kwargs["duration_seconds"] == duration
    assert kwargs["creative_brief"] == "不要转身。"
    assert kwargs["variant_count"] == 1
    assert kwargs["analyze_product_images"] is False
    assert [(x.asset_id, x.role) for x in kwargs["storyboard_images"]] == [
        (str(h.uploads[0].id), "STORYBOARD"),
    ]
    assert [(x.asset_id, x.role) for x in kwargs["product_images"]] == [
        (str(h.uploads[1].id), "PRODUCT"),
    ]
    assert kwargs["product_images"][0].image_url == ""
    assert analysis._analysis_image_url.await_count == 1
    assert task.compiled_plan_json["compiler"] == ootd_simple.PROFILE
    assert task.compiled_plan_json["variants"][0]["master_prompt"] == PROMPT
    assert task.variants[0].prompt_text == PROMPT
    assert task.settings_json["autoSubmitToCutflow"] is True
    assert task.settings_json["sound"] is False
    assert task.settings_json["subtitles"] is False
    attempts = await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    assert len(attempts) == 1
    request = h.adapter.validate_request.call_args.args[0]
    assert request.director_native_profile == ootd_simple.PROFILE
    assert request.prompt == PROMPT
    assert request.duration == duration
    assert request.audio == "off"
    assert request.multi_shot is True
    assert len(request.image_paths) == len(request.product_reference_paths) == 1
    assert request.image_paths[0].read_bytes() == b"first.png"
    assert request.product_reference_paths[0].read_bytes() == b"product.png"
    cli, calls = _cli(h.settings, monkeypatch)
    await cli.submit(request)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[-1] == PROMPT  # Includes @image_N: no extra prefix or reference rewriting.
    for flag, value in (("--duration", str(duration)), ("--prefer_multi_shots", "true"),
                        ("--enable_audio", "false"), ("--model", MODEL)):
        assert argv[argv.index(flag) + 1] == value
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--image"] == [
        f"https://uploads.invalid/{path.name}"
        for path in (*request.image_paths, *request.product_reference_paths)
    ]


async def test_ootd_opt_in_product_analysis_does_not_change_original_kling_images(harness):
    h = harness
    task = await h.create(h.payload(solAnalyzeProductImages=True))
    assert h.planner.call_args.kwargs["analyze_product_images"] is True
    assert h.planner.call_args.kwargs["product_images"][0].image_url.endswith("product.png")
    assert analysis._analysis_image_url.await_count == 2
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    request = h.adapter.validate_request.call_args.args[0]
    assert request.image_paths[0].read_bytes() == b"first.png"
    assert request.product_reference_paths[0].read_bytes() == b"product.png"


@pytest.mark.parametrize("raw_prompt", [
    "10s first frame and exact artwork.\n\n0-3s show the print.\nDIRECT CUT\n"
    "3-6s close-up on lettering.\nDIRECT CUT\n6-10s show the fit.",
    "10s original framing.\r\n\r\n0-2s show front.\r\n"
    "DIRECT CUT\r\n2-6s detail.\r\nDIRECT CUT\r\n6-10s turn.",
])
async def test_multiline_director_prompt_survives_kling_cli_cmd_transport(
    harness, monkeypatch, raw_prompt,
):
    h = harness
    task = await h.create(h.payload(durationSeconds=10))
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    original_request = h.adapter.validate_request.call_args.args[0]
    assert raw_prompt.count("DIRECT CUT") == 2
    cli, calls = _cli(h.settings, monkeypatch)
    await cli.submit(replace(original_request, prompt=raw_prompt))
    assert len(calls) == 1
    sent = calls[0][-1]
    assert "\n" not in sent and "\r" not in sent
    assert sent == " ".join(raw_prompt.splitlines())
    assert sent.count("DIRECT CUT") == 2
    assert "close-up" in sent or "detail" in sent


@pytest.mark.parametrize("role", list(UserRole))
async def test_ootd_has_no_pilot_account_gate(harness, role):
    h = harness
    h.owner.role = role
    await h.session.commit()
    task = await h.create()
    assert task.settings_json["directorNativeProfile"] == ootd_simple.PROFILE
    h.planner.assert_awaited_once()


@pytest.mark.parametrize("code", ["AI_CREATIVE_PROVIDER_ERROR", "OOTD_PROMPT_INVALID"])
async def test_sol_failure_never_queues_or_claims_uploads_and_session_remains_usable(harness, code):
    h = harness
    h.planner.side_effect = CreativePlannerError("offline fault", code=code, request_attempts=1)
    with pytest.raises(fixed_video_tasks.FixedVideoTaskValidationError) as caught:
        await h.create()
    assert caught.value.code == code
    h.planner.assert_awaited_once()
    h.queue.assert_not_called()
    for model in (ParentTask, GenerationAttempt, GenerationCommand):
        assert await h.session.scalar(select(func.count()).select_from(model)) == 0
    assert all(upload.claimed_task_id is None for upload in h.uploads)
    # Explicit later user attempt may succeed; no automatic retry occurs on failure.
    h.planner.side_effect = None
    assert (await h.create()).variants[0].prompt_text == PROMPT


@pytest.mark.parametrize("changes,code", [
    ({"paidSubmissionConsent": False}, "PAID_SUBMISSION_CONSENT_REQUIRED"),
    ({"durationSeconds": 4}, "OOTD_ROUTE_UNAVAILABLE"),
    ({"cutMode": "continuous_when_possible"}, "OOTD_ROUTE_UNAVAILABLE"),
])
async def test_invalid_ootd_route_or_missing_consent_never_falls_back(harness, changes, code):
    with pytest.raises(fixed_video_tasks.FixedVideoTaskValidationError) as caught:
        await harness.create(harness.payload(**changes))
    assert caught.value.code == code
    harness.planner.assert_not_called()
    harness.queue.assert_not_called()


async def test_multiple_storyboards_reach_cli_separately(harness, monkeypatch):
    h = harness
    frames = [{"uploadId": str(h.uploads[i].id), "order": order}
              for order, i in enumerate((0, 2), 1)]
    prompt = PROMPT.replace("@image_2 是商品", "@image_2 是分镜参考，@image_3 是商品")
    h.planner.return_value = (replace(_variant(), prompt=prompt),)
    task = await h.create(h.payload(storyboards=frames))
    h.planner.assert_awaited_once()
    kwargs = h.planner.call_args.kwargs
    assert [(x.asset_id, x.order, x.role) for x in kwargs["storyboard_images"]] == [
        (str(h.uploads[0].id), 1, "STORYBOARD"),
        (str(h.uploads[2].id), 2, "STORYBOARD"),
    ]
    assert [(x.asset_id, x.role) for x in kwargs["product_images"]] == [
        (str(h.uploads[1].id), "PRODUCT"),
    ]
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    request = h.adapter.validate_request.call_args.args[0]
    assert request.prompt == prompt
    assert [x.read_bytes() for x in request.image_paths] == [b"first.png", b"extra.png"]
    assert [x.read_bytes() for x in request.product_reference_paths] == [b"product.png"]
    cli, calls = _cli(h.settings, monkeypatch)
    await cli.submit(request)
    argv = calls[0]
    assert task.variants[0].prompt_text == prompt
    assert "图片1=唯一视频起始首帧" in argv[-1]
    assert "图片2=后续镜头参考" in argv[-1]
    assert "图片3=商品主参考图" in argv[-1]
    assert "@image_" not in argv[-1]
    assert "图片2 是分镜参考，图片3 是商品" in argv[-1]
    assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--image"] == [
        f"https://uploads.invalid/{path.name}"
        for path in (*request.image_paths, *request.product_reference_paths)
    ]


async def test_ootd_two_storyboards_two_products_keep_roles_and_cli_order(harness, monkeypatch):
    h = harness
    frames = [{"uploadId": str(h.uploads[index].id), "order": order}
              for order, index in enumerate((0, 2), 1)]
    products = [{"uploadId": str(h.uploads[index].id), "order": order}
                for order, index in enumerate((1, 3), 1)]
    prompt = (
        "10秒真实手机视频。@image_1 为第一帧；自然整理衣袖，直接切镜。"
        "参考 @image_2 的景别展示正面印花，@image_3 是主产品图，"
        "@image_4 是补充产品图。保持现有场景与人物，动作自然，无字幕。"
    )
    h.planner.return_value = (replace(_variant(), prompt=prompt),)
    task = await h.create(h.payload(storyboards=frames, subjectImages=products))
    kwargs = h.planner.call_args.kwargs
    assert [(x.asset_id, x.role) for x in kwargs["storyboard_images"]] == [
        (str(h.uploads[0].id), "STORYBOARD"),
        (str(h.uploads[2].id), "STORYBOARD"),
    ]
    assert [(x.asset_id, x.role) for x in kwargs["product_images"]] == [
        (str(h.uploads[1].id), "PRODUCT"),
        (str(h.uploads[3].id), "PRODUCT"),
    ]
    assert task.variants[0].prompt_text == prompt
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    request = h.adapter.validate_request.call_args.args[0]
    assert [x.read_bytes() for x in request.image_paths] == [b"first.png", b"extra.png"]
    assert [x.read_bytes() for x in request.product_reference_paths] == [
        b"product.png", b"product-back.png",
    ]
    cli, calls = _cli(h.settings, monkeypatch)
    await cli.submit(request)
    assert len(calls) == 1
    argv = calls[0]
    assert [argv[i + 1] for i, item in enumerate(argv) if item == "--image"] == [
        f"https://uploads.invalid/{path.name}"
        for path in (*request.image_paths, *request.product_reference_paths)
    ]
    assert argv[argv.index("--prefer_multi_shots") + 1] == "true"
    assert argv[argv.index("--enable_audio") + 1] == "false"
    assert request.prompt == prompt  # Stored Sol text is never rewritten.
    assert "@image_" not in argv[-1]
    assert "\n" not in argv[-1]
    for role in (
        "图片1=唯一视频起始首帧", "图片2=后续镜头参考",
        "图片3=商品主参考图", "图片4=商品细节补充参考图",
        "图片1 为第一帧", "图片2 的景别", "图片3 是主产品图", "图片4 是补充产品图",
    ):
        assert role in argv[-1]
    assert "直接切镜" in argv[-1]

    cli, calls = _cli(h.settings, monkeypatch)
    with pytest.raises(KlingCliError) as exc:
        await cli.submit(replace(request, prompt=prompt + " @image_5"))
    assert exc.value.code == "KLING_IMAGE_REFERENCE_UNAVAILABLE"
    assert calls == []  # Invalid image slots fail before any paid CLI call.


async def test_ootd_disables_legacy_usp_preview_before_any_work(harness):
    from fastapi import HTTPException
    from libtv_agent.api.creative_v25_preview import preview_creative_v25

    harness.owner.role = UserRole.ADMIN
    harness.owner.username = "classic-admin"
    session = SimpleNamespace(get=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=harness.settings,
    )))
    with pytest.raises(HTTPException) as caught:
        await preview_creative_v25(None, harness.owner, session, request)
    assert caught.value.status_code == 410
    assert caught.value.detail["code"] == "USP_PREVIEW_DISABLED"
    session.get.assert_not_called()


async def test_custom_prompt_keeps_existing_reference_and_brief_contract(harness):
    h = harness
    prompt = "Keep the original outfit. Show the sleeves without turning around."
    task = await h.create(h.payload(generationMode="custom", customPrompt=prompt,
                                    autoSubmitToCutflow=False))
    h.planner.assert_not_called()
    assert task.compiled_plan_json["compiler"] == fixed_video_tasks.CUSTOM_PROMPT_COMPILER_VERSION
    assert task.compiled_plan_json["userPrompt"] == prompt
    assert task.variants[0].prompt_text.startswith(prompt)
    assert task.variants[0].prompt_text.endswith("补充要求：不要转身。")
    assert "primary product reference" in task.variants[0].prompt_text
    assert "directorNativeProfile" not in task.settings_json
    assert task.settings_json["autoSubmitToCutflow"] is False


@pytest.mark.parametrize("status", [AttemptStatus.DISPATCH_PENDING,
                                   AttemptStatus.UNKNOWN_AFTER_SUBMIT, AttemptStatus.SUCCEEDED])
async def test_preparation_reuses_existing_submission_even_without_cloud_id(harness, status):
    h = harness
    task = await h.create()
    first = await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    first[0].status = status
    await h.session.commit()
    second = await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    assert second[0].id == first[0].id
    assert await h.session.scalar(select(func.count()).select_from(GenerationAttempt)) == 1
    assert second[0].status == status


async def test_missing_cli_receipt_is_uncertain_and_never_resubmitted(harness, monkeypatch):
    h = harness
    task = await h.create()
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    cli, calls = _cli(h.settings, monkeypatch, submission={"status": "submitted"})
    with pytest.raises(KlingCliError) as caught:
        await cli.submit(h.adapter.validate_request.call_args.args[0])
    assert caught.value.code == "KLING_SUBMIT_RESULT_UNKNOWN"
    assert caught.value.submitted is True
    assert caught.value.uncertain is True
    assert len(calls) == 1


@pytest.mark.parametrize("missing_argument", ["enable_audio", "prefer_multi_shots", "duration"])
async def test_ootd_missing_cli_capability_fails_closed_before_process(
    harness, monkeypatch, missing_argument,
):
    h = harness
    task = await h.create()
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    request = h.adapter.validate_request.call_args.args[0]
    cli, calls = _cli(h.settings, monkeypatch, missing_argument=missing_argument)
    with pytest.raises(KlingCliError) as caught:
        await cli.submit(request)
    assert caught.value.code == "OOTD_CLI_CONTRACT_UNSUPPORTED"
    assert not caught.value.submitted
    assert calls == []


async def test_ootd_product_reference_is_optional(harness):
    h = harness
    task = await h.create(h.payload(subjectImages=[]))
    assert h.planner.call_args.kwargs["product_images"] == []
    await prepare_kling_task(h.session, h.adapter, h.store, task, h.settings)
    request = h.adapter.validate_request.call_args.args[0]
    assert len(request.image_paths) == 1
    assert request.product_reference_paths == ()
    assert task.variants[0].prompt_text == PROMPT


@pytest.mark.parametrize("focus,has_product", [("print", True), ("back", False)])
async def test_ootd_controls_reach_planner_and_task_snapshot(harness, focus, has_product):
    h = harness
    changes = {} if has_product else {"subjectImages": []}
    task = await h.create(h.payload(displayFocus=focus, actionIntensity="high", **changes))
    kwargs = h.planner.call_args.kwargs
    assert kwargs["display_focus"] == focus
    assert kwargs["analyze_product_images"] is has_product
    assert kwargs["action_intensity"] == "high"
    assert kwargs["brand_tone"] == get_site_style_profile("pagewings").tone[:240]
    if has_product:
        assert kwargs["product_images"][0].image_url
    controls = task.settings_json["ootdCreativeControls"]
    assert controls["displayFocus"] == focus
    assert controls["productImagesAnalyzed"] is has_product
    assert controls["actionIntensity"] == "high"
    assert controls["revision"] == "ootd-natural-baseline-restored-v1"
    assert controls["brandToneApplied"] is False
    assert task.variants[0].generation_config_json["ootdCreativeControls"] == controls
    assert task.variants[0].prompt_text == PROMPT


async def test_new_default_does_not_change_existing_request_hash(harness):
    h = harness
    old_body = h.payload().model_dump(mode="json", by_alias=True)
    assert "displayFocus" not in old_body
    new_default = h.payload(displayFocus="overall").model_dump(mode="json", by_alias=True)
    assert new_default == old_body
    detail = h.payload(displayFocus="print").model_dump(mode="json", by_alias=True)
    assert detail["displayFocus"] == "print"
    assert VideoTaskCreate.model_validate(detail).display_focus == "print"


@pytest.mark.parametrize("setting,value", [
    ("kling_provider_backend", "api"),
    ("openai_model", "gpt-5.6-luna"),
    ("ai_adapter_mode", "fake"),
])
async def test_unavailable_ootd_provider_or_model_does_not_switch_routes(harness, setting, value):
    h = harness
    setattr(h.settings, setting, value)
    with pytest.raises(fixed_video_tasks.FixedVideoTaskValidationError) as caught:
        await h.create()
    assert caught.value.code == "OOTD_ROUTE_UNAVAILABLE"
    h.planner.assert_not_called()
    h.queue.assert_not_called()
