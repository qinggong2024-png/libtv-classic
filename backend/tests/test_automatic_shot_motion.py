import base64
import io
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.motion_facts import (
    ImageMotionFacts,
    MotionFactsResult,
    OpenAIMotionFactsAdapter,
)
from libtv_agent.adapters.object_store import LocalObjectStore
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import Base, StagedUpload, VisualFactCache
from libtv_agent.services import automatic_motion_facts, fixed_video_tasks
from libtv_agent.services.automatic_motion_facts import ObservedMotion, observe_storyboards
from libtv_agent.services.prompt_compiler import PromptCompileInput, compile_video_plan
from libtv_agent.services.shot_motion import plan_shot_motion
from libtv_agent.templates.facial_expressions import FaceEvidence
from libtv_agent.templates.motion_actions import ACTION_LIBRARY, ActionContext, action_family
from libtv_agent.templates.site_style_profiles import (
    get_site_style_profile,
    list_site_style_profiles,
)
from libtv_agent.templates.video_templates import get_video_template
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def facts(asset_id, *capabilities):
    return ImageMotionFacts(
        asset_id=asset_id,
        observations=[
            {
                "capability": capability,
                "state": "visible",
                "confidence": "high",
                "evidence": "Directly visible in this image",
            }
            for capability in capabilities
        ],
    )


def test_uncertain_contradictory_and_static_camera_claims_remain_unknown():
    observed = facts("one", "standing_pose", "seated_pose", "fixed_camera", "free_hand")
    observed.observations[3].confidence = "uncertain"
    assert not observed.context().confirmed
    duplicated = facts("two", "front_view", "front_view")
    assert not duplicated.context().confirmed


async def test_visual_adapter_validates_identity_and_sends_image_payload():
    result = MotionFactsResult(images=[facts("a", "seated_pose"), facts("b", "standing_pose")])
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=result))
    adapter = OpenAIMotionFactsAdapter(
        Settings(_env_file=None),
        SimpleNamespace(responses=SimpleNamespace(parse=parse)),
    )
    inputs = [AnalysisImageInput(id_, 1, f"data:image/jpeg;base64,{id_}") for id_ in ("a", "b")]
    assert await adapter.analyze(inputs) == result
    content = parse.call_args.kwargs["input"][1]["content"]
    assert [p["image_url"] for p in content if p["type"] == "input_image"] == [
        i.image_url for i in inputs
    ]
    parse.return_value = SimpleNamespace(output_parsed=MotionFactsResult(images=[facts("a")] * 2))
    with pytest.raises(ValueError, match="exact input images"):
        await adapter.analyze(inputs)


async def test_visual_cache_uses_real_bytes_and_isolates_user_brand_model_and_failure(
    tmp_path,
    monkeypatch,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[VisualFactCache.__table__])
        )
    settings = Settings(
        app_env="test",
        ai_adapter_mode="openai",
        openai_api_key="synthetic-test-key",
        artifact_root=tmp_path,
        _env_file=None,
    )
    monkeypatch.setattr(automatic_motion_facts, "paid_generation_authorized", lambda _: True)
    store = LocalObjectStore(tmp_path)
    Image.new("RGB", (128, 128)).save(tmp_path / "one.jpg")
    upload = StagedUpload(id=uuid.uuid4(), sha256="a" * 64, object_key="one.jpg")

    async def parse(**kwargs):
        content = kwargs["input"][1]["content"]
        image_data = content[1]["image_url"].split(",", 1)[1]
        assert base64.b64decode(image_data) == (tmp_path / "one.jpg").read_bytes()
        key = content[0]["text"].split("=", 1)[1]
        return SimpleNamespace(output_parsed=MotionFactsResult(images=[facts(key, "seated_pose")]))

    mocked_parse = AsyncMock(side_effect=parse)
    client = SimpleNamespace(responses=SimpleNamespace(parse=mocked_parse))
    monkeypatch.setattr(
        automatic_motion_facts,
        "OpenAIMotionFactsAdapter",
        lambda config: OpenAIMotionFactsAdapter(config, client),
    )
    user, brand = uuid.uuid4(), uuid.uuid4()
    async with async_sessionmaker(engine)() as session:

        async def observe(user_id=user, brand_id=brand, config=settings):
            return (
                await observe_storyboards(
                    session,
                    uploads=[upload],
                    user_id=user_id,
                    brand_id=brand_id,
                    settings=config,
                    object_store=store,
                )
            )[0]

        assert (await observe()).source == "automatic_vision"
        assert (await observe()).source == "automatic_vision_cache"
        assert mocked_parse.await_count == 1
        await observe(user_id=uuid.uuid4())
        await observe(brand_id=uuid.uuid4())
        await observe(config=settings.model_copy(update={"openai_model": "another-model"}))
        assert mocked_parse.await_count == 4
        mocked_parse.side_effect = TimeoutError("synthetic provider failure")
        new_user = uuid.uuid4()
        for _ in range(2):
            fallback = await observe(user_id=new_user)
            assert fallback.source == "unknown"
            assert fallback.reason == "analysis_unavailable"
            assert not fallback.context.confirmed
        assert mocked_parse.await_count == 6  # Failed calls were not cached.
    await engine.dispose()


@pytest.mark.parametrize("mode,key", [("fake", "synthetic"), ("openai", None)])
async def test_disabled_analysis_never_constructs_provider(mode, key, monkeypatch):
    def fail(_):
        raise AssertionError("disabled provider must never be constructed")

    monkeypatch.setattr(automatic_motion_facts, "OpenAIMotionFactsAdapter", fail)
    result = await observe_storyboards(
        None,
        uploads=[StagedUpload()],
        user_id=uuid.uuid4(),
        brand_id=None,
        settings=Settings(ai_adapter_mode=mode, openai_api_key=key, _env_file=None),
        object_store=None,
    )
    assert result == [ObservedMotion(reason="analysis_disabled")]


def plan(
    capabilities=(),
    *,
    duration=8,
    pool=("stance_shift",),
    micro=(),
    library=None,
    denied=(),
    template_id="mood_model",
):
    return plan_shot_motion(
        profile=replace(
            get_site_style_profile("pagewings"), primary_motion_pool=pool, micro_motion_pool=micro
        ),
        template_id=template_id,
        context=ActionContext.from_values(confirmed=capabilities, denied=denied),
        duration=duration,
        variant_index=1,
        library=library,
        template_motion_pool=(),
        template_detail_pool=(),
    )


def test_known_pose_with_unknown_hand_path_keeps_adaptable_action():
    text, selected = plan(
        ("seated_pose", "mirror_selfie", "phone_hold_visible"),
        pool=("stance_shift", "hem_adjust", "over_shoulder_back"),
    )
    assert selected["selectedActionIds"][0] == "hem_adjust"
    assert "stance_shift" not in selected["selectedActionIds"]
    assert "保持镜自拍持机手与镜面关系" in text
    assert "safe_hand_path" not in text
    assert "表情：" not in text


def test_unknown_pose_keeps_a_direct_low_risk_motion_without_internal_tokens():
    text, selected = plan((), pool=("stance_shift",))
    assert selected["selectedActionIds"] == ["stance_shift"]
    assert "standing_pose" not in text
    assert "前提成立时" not in text
    assert "将重心明确移到一侧" in text


def test_unknown_space_keeps_one_complete_creative_motion():
    text, selected = plan((), pool=("controlled_walk",))
    assert selected["selectedActionIds"] == ["controlled_walk"]
    assert "walk_depth" not in text
    assert "前提不成立时" not in text
    assert "以有目的的两到四步走向镜头" in text


def test_ootd_unknown_facts_compile_two_visible_beats_without_provider_jargon():
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=1,
            result_count=1,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=8,
            site_profile=get_site_style_profile("tokyotiger"),
            shot_action_contexts=(ActionContext(),),
            shot_evidence_sources=("unknown",),
        )
    )

    selection = compiled.variant_seed["shotMotionSelections"][0]
    assert selection["selectedActionIds"] == [
        "controlled_walk",
        "print_detail_pause",
    ]
    assert [beat["actionId"] for beat in selection["motionBeats"]] == [
        "controlled_walk",
        "print_detail_pause",
    ]
    assert selection["motionBeats"][0]["startSeconds"] == 0
    assert selection["motionBeats"][-1]["endSeconds"] == 8
    assert not selection["expressionChange"]
    first_action_id, last_action_id = compiled.variant_seed["ugcActionIds"]
    assert f"1. 模特自然展示穿搭：{ACTION_LIBRARY[first_action_id].label}" in compiled.master_prompt
    assert "2. 模特展示衣服细节" in compiled.master_prompt
    assert f"3. 模特自然收尾展示：{ACTION_LIBRARY[last_action_id].label}" in compiled.master_prompt
    assert compiled.master_prompt.count("2. 模特展示衣服细节") == 1
    assert "front_print_visible" not in compiled.master_prompt
    assert "前提成立时" not in compiled.master_prompt
    assert "不根据文字描述猜测细节" not in compiled.master_prompt
    assert "仅改变动作细节" not in compiled.master_prompt
    assert "不强制起身" not in compiled.master_prompt
    assert "武士表演" not in compiled.master_prompt
    assert "霓虹赛博" not in compiled.master_prompt
    assert "standard_to_fast" not in compiled.master_prompt
    assert "缺少自然快慢层次" not in compiled.negative_prompt
    assert "动作包含：" in compiled.master_prompt


@pytest.mark.parametrize(
    "duration",
    [8, 10, 15],
)
def test_ootd_duration_drives_balanced_multibeat_sequence(duration):
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=1,
            result_count=4,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=duration,
            site_profile=get_site_style_profile("tokyotiger"),
            shot_action_contexts=(ActionContext(),),
            shot_evidence_sources=("unknown",),
        )
    )

    selection = compiled.variant_seed["shotMotionSelections"][0]
    beats = selection["motionBeats"]
    expected_beat_count = 2 if duration <= 8 else 3 if duration <= 12 else 4
    assert len(selection["selectedActionIds"]) == expected_beat_count
    assert len(beats) == expected_beat_count
    assert any(
        action_family(action_id) in {"walking", "turning", "standing_attitude"}
        for action_id in selection["selectedActionIds"]
    )
    assert any(
        action_family(action_id) in {"product_interaction", "garment_detail"}
        for action_id in selection["selectedActionIds"]
    )
    assert beats[0]["startSeconds"] == 0
    assert beats[-1]["endSeconds"] == duration
    durations = [
        round(beat["endSeconds"] - beat["startSeconds"], 2)
        for beat in beats
    ]
    assert all(beat_duration >= 2.9 for beat_duration in durations)
    assert max(durations) - min(durations) <= 2.2


def test_all_active_brands_templates_and_durations_use_duration_aware_beats():
    active_profiles = [
        profile for profile in list_site_style_profiles() if profile.status == "active"
    ]
    for template in (get_video_template("ootd_walk"), get_video_template("garment_detail"),
                     get_video_template("mood_model")):
        for profile in active_profiles:
            for duration in range(3, 16):
                compiled = compile_video_plan(
                    PromptCompileInput(
                        template=template,
                        storyboard_count=1,
                        result_count=1,
                        action_intensity="standard",
                        camera_tempo="standard",
                        cut_mode="explicit_cuts",
                        variant_index=1,
                        duration_seconds=duration,
                        site_profile=profile,
                        shot_action_contexts=(ActionContext(),),
                        shot_evidence_sources=("unknown",),
                        prompt_budget=2650,
                    )
                )
                selection = compiled.variant_seed["shotMotionSelections"][0]
                expected_beats = (
                    1 if duration < 6 else 2 if duration <= 8 else 3 if duration <= 12 else 4
                )
                assert len(selection["selectedActionIds"]) == expected_beats, (
                    template.id,
                    profile.id,
                    duration,
                    selection["selectedActionIds"],
                )
                assert selection["motionBeats"][0]["startSeconds"] == 0
                assert selection["motionBeats"][-1]["endSeconds"] == duration
                assert "front_print_visible" not in compiled.master_prompt
                assert "前提不成立" not in compiled.master_prompt
                assert len(compiled.master_prompt) <= 2650


def test_ootd_multi_storyboard_manual_upload_keeps_visible_motion():
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=3,
            result_count=1,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=10,
            site_profile=get_site_style_profile("tokyotiger"),
            shot_action_contexts=(ActionContext(), ActionContext(), ActionContext()),
            shot_evidence_sources=("unknown", "unknown", "unknown"),
            selection_salt=7,
        )
    )

    selections = compiled.variant_seed["shotMotionSelections"]
    assert len(selections) == 3
    assert all(
        any(
            action_family(action_id)
            in {"walking", "turning", "standing_attitude"}
            for action_id in selection["selectedActionIds"]
        )
        for selection in selections
    )
    assert "动作包含：" in compiled.master_prompt
    assert "2. 模特展示衣服细节" in compiled.master_prompt
    assert len(compiled.variant_seed["ugcActionIds"]) == 2


def test_task_selection_salt_varies_actions_across_users_brands_and_runs():
    profiles = [profile for profile in list_site_style_profiles() if profile.status == "active"]
    first_actions = set()
    sequences = set()
    ugc_sequences = set()
    for profile in profiles:
        for selection_salt in range(8):
            compiled = compile_video_plan(
                PromptCompileInput(
                    template=get_video_template("ootd_walk"),
                    storyboard_count=1,
                    result_count=1,
                    action_intensity="standard",
                    camera_tempo="standard",
                    cut_mode="explicit_cuts",
                    variant_index=1,
                    duration_seconds=10,
                    site_profile=profile,
                    shot_action_contexts=(ActionContext(),),
                    shot_evidence_sources=("unknown",),
                    selection_salt=selection_salt,
                )
            )
            actions = tuple(
                compiled.variant_seed["shotMotionSelections"][0]["selectedActionIds"]
            )
            first_actions.add(actions[0])
            sequences.add(actions)
            ugc_sequences.add(tuple(compiled.variant_seed["ugcActionIds"]))

    assert len(first_actions) >= 4
    assert len(sequences) >= 8
    assert len(ugc_sequences) >= 8
    assert all(
        any(
            action_family(action_id) in {"walking", "turning", "standing_attitude"}
            for action_id in actions
        )
        for actions in sequences
    )


@pytest.mark.parametrize("intensity", ["low", "standard", "high"])
def test_action_intensity_keeps_the_ugc_action_structure(intensity):
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=1,
            result_count=1,
            action_intensity=intensity,
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=10,
            site_profile=get_site_style_profile("tokyotiger"),
            shot_action_contexts=(ActionContext(),),
            shot_evidence_sources=("unknown",),
            selection_salt=5,
        )
    )

    assert "动作包含：" in compiled.master_prompt
    assert compiled.master_prompt.count("模特展示衣服细节") == 1
    assert len(compiled.variant_seed["ugcActionIds"]) == 2


def test_ootd_multibeat_adds_one_small_expression_only_with_face_evidence():
    profile = replace(
        get_site_style_profile("tokyotiger"),
        expression_pool=("自然浅笑",),
    )
    context = ActionContext.from_values(
        confirmed=("standing_pose", "walk_depth", "face_visible", "face_gaze_adjustable")
    )
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=1,
            result_count=1,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=8,
            site_profile=profile,
            shot_action_contexts=(context,),
            shot_evidence_sources=("automatic_vision",),
            shot_face_evidence=(FaceEvidence(mouth="neutral", eyes_visible=True),),
        )
    )

    selection = compiled.variant_seed["shotMotionSelections"][0]
    assert selection["expressionChange"]
    assert selection["selectedExpressionId"] == "warm_half_smile"
    assert [beat["actionId"] for beat in selection["motionBeats"]] == [
        "controlled_walk",
        "print_detail_pause",
    ]
    assert "2. 模特展示衣服细节" in compiled.master_prompt


def test_explicitly_denied_space_still_excludes_conditional_creative_motion():
    _, selected = plan((), pool=("controlled_walk",), denied=("walk_depth",))
    assert selected["selectedActionIds"] == ["breath_settle"]


def test_explicit_denial_still_blocks_motion_that_requires_the_denied_capability():
    _, selected = plan(
        (),
        pool=("hem_adjust",),
        denied=("safe_hand_path",),
    )
    assert selected["selectedActionIds"] == ["breath_settle"]


def test_duration_and_inherited_requirements_cannot_be_weakened_by_catalog_override():
    text, selection = plan(("standing_pose", "walk_depth"), duration=4, pool=("controlled_walk",))
    assert selection["fallback"]
    override = replace(ACTION_LIBRARY["stance_shift"], required_capabilities=())
    assert not plan(library={"stance_shift": override})[1]["fallback"]
    assert plan(("standing_pose",), duration=3)[1]["selectedActionIds"] == ["stance_shift"]
    capabilities = ("standing_pose", "face_visible", "face_gaze_adjustable")
    assert (
        plan(capabilities, duration=4, micro=("controlled_gaze",))[1]["selectedMicroActionId"]
        is None
    )
    assert (
        plan(capabilities, duration=5, micro=("controlled_gaze",))[1]["selectedMicroActionId"]
        == "controlled_gaze"
    )


def test_fixed_camera_allows_stable_print_display_and_preserves_source_capture():
    text, selected = plan(("fixed_camera", "front_print_visible"), pool=("print_detail_pause",))
    assert selected["selectedActionIds"] == ["print_detail_pause"]
    assert "在连续的重心与肩身变化中让印花清楚可读" in text
    assert "不做突然定格" in text
    assert "保持固定机位，不推拉" in text


def test_micro_motion_does_not_repeat_face_reactions_or_inherit_hidden_hand_use():
    face = ("face_visible", "face_gaze_adjustable")
    assert (
        plan(face, pool=("eye_contact_soft_smile",), micro=("controlled_gaze",))[1][
            "selectedMicroActionId"
        ]
        is None
    )
    hand = replace(
        ACTION_LIBRARY["hem_adjust"],
        id="brand_hem",
        base_action_id="hem_adjust",
        required_capabilities=(),
    )
    library = {**ACTION_LIBRARY, "brand_hem": hand}
    assert (
        plan(
            ("standing_pose", "free_hand", "hem_reachable", "safe_hand_path"),
            micro=("brand_hem",),
            library=library,
        )[1]["selectedMicroActionId"]
        is None
    )


def test_per_shot_conditions_do_not_leak_and_prompts_fit_all_supported_counts():
    for profile in list_site_style_profiles():
        if profile.status != "active":
            continue
        for count in range(1, 8):
            for variant in range(1, 5):
                contexts = (ActionContext.from_values(confirmed=("standing_pose",)),) + (
                    (ActionContext(),) * (count - 1)
                )
                compiled = compile_video_plan(
                    PromptCompileInput(
                        template=get_video_template("ootd_walk"),
                        storyboard_count=count,
                        result_count=4,
                        variant_index=variant,
                        duration_seconds=15,
                        action_intensity="standard",
                        camera_tempo="standard",
                        cut_mode="explicit_cuts",
                        site_profile=profile,
                        shot_action_contexts=contexts,
                        prompt_budget=2650,  # Reserve room for provider reference roles.
                    )
                )
                assert len(compiled.master_prompt) <= 2650
                assert "轮换自然动作与表情" not in compiled.master_prompt
                assert compiled.master_prompt.startswith("生成一段动态视频，智能分镜。")
                assert "动作包含：" in compiled.master_prompt
                assert compiled.master_prompt.count("模特展示衣服细节") == 1


def test_creative_brief_motion_selection_avoids_repeating_actions():
    compiled = compile_video_plan(
        PromptCompileInput(
            template=get_video_template("ootd_walk"),
            storyboard_count=3,
            result_count=1,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            variant_index=1,
            duration_seconds=14,
            site_profile=get_site_style_profile("tokyotiger"),
            shot_action_contexts=(ActionContext(), ActionContext(), ActionContext()),
            shot_evidence_sources=("unknown", "unknown", "unknown"),
            creative_brief="开箱＋穿搭展示，最后转身展示背面",
        )
    )
    selections = compiled.variant_seed["shotMotionSelections"]
    actions = [item["selectedPrimaryActionId"] for item in selections]
    assert len(set(actions)) > 1
    assert "动作包含：" in compiled.master_prompt
    assert len(compiled.variant_seed["ugcActionIds"]) == 2
    assert compiled.master_prompt.count("最后转身展示背面") == 1


def test_package_brand_scope_and_authorized_nurse_status():
    for brand in ("nerdywave", "geeksoutfit", "pagewings"):
        profile = get_site_style_profile(brand)
        assert profile.version == "1.2.1"
        assert "2026-10-15" in profile.research_note
        assert "未重新核验官网" in profile.research_note
        assert not profile.preferred_environment
        assert set(profile.primary_motion_pool + profile.micro_motion_pool) <= ACTION_LIBRARY.keys()
    assert get_site_style_profile("goodnurselife").status == "active"


async def test_automatic_task_uses_server_facts_and_idempotency_without_confirmation(
    tmp_path, monkeypatch
):
    calls = []

    async def observe(session, **kwargs):
        calls.append(kwargs["uploads"])
        return [
            ObservedMotion(
                ActionContext.from_values(confirmed=("standing_pose",)), "automatic_vision"
            )
        ]

    monkeypatch.setattr(fixed_video_tasks, "observe_storyboards", observe)
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        artifact_root=tmp_path,
        runner_root=tmp_path / "runner",
        object_store_mode="local",
        kling_admin_config_file=tmp_path / "config.json",
        _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            image = io.BytesIO()
            Image.new("RGB", (360, 640)).save(image, format="PNG")
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "upload"}
            uploaded = await client.post(
                "/api/v1/uploads",
                headers=headers,
                files={"file": ("one.png", image.getvalue(), "image/png")},
            )
            assert uploaded.status_code == 201
            headers["Idempotency-Key"] = "automatic-motion"
            payload = {
                "clientTaskId": "automatic-motion",
                "title": "Auto motion",
                "storyboards": [
                    {
                        "uploadId": uploaded.json()["id"],
                        "order": 1,
                        "actionFacts": {"confirmed": ["back_reference"]},
                    }
                ],
                "templateId": "ootd_walk",
                "siteProfileId": "nerdywave",
                "resultCount": 1,
                "durationSeconds": 8,
                "actionIntensity": "standard",
                "cutMode": "explicit_cuts",
                "paidSubmissionConsent": True,
            }
            for _ in range(2):
                response = await client.post("/api/v1/video-tasks", headers=headers, json=payload)
                assert response.status_code == 201, response.text
                task = response.json()
                observed = task["materialValidation"]["storyboardActionFacts"][0]
                assert observed["source"] == "automatic_vision"
                assert observed["confirmed"] == ["standing_pose"]  # Client claims ignored.
                assert task["materialValidation"]["visionCheckRequired"] is False
                selected = task["compiledPlan"]["variants"][0]["variant_seed"][
                    "shotMotionSelections"
                ][0]
                actions = selected["selectedActionIds"]
                assert len(actions) == 2
                assert selected["selectedPrimaryActionId"] == actions[0]
                assert any(
                    action_family(action_id) in {"walking", "turning", "standing_attitude"}
                    for action_id in actions
                )
                assert action_family(actions[-1]) in {
                    "product_interaction",
                    "garment_detail",
                }
                assert selected["evidenceSource"] == "automatic_vision"
                prompt = task["compiledPlan"]["variants"][0]["master_prompt"]
                assert "front_print_visible" not in prompt
                assert "前提不成立时" not in prompt
            assert len(calls) == 1
