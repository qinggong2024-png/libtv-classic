import io
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.motion_facts import FacialObservation, ImageMotionFacts, MotionFactsResult
from libtv_agent.adapters.object_store import LocalObjectStore
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import Base, StagedUpload, VisualFactCache
from libtv_agent.services import automatic_motion_facts, fixed_video_tasks
from libtv_agent.services.automatic_motion_facts import ObservedMotion, observe_storyboards
from libtv_agent.services.prompt_compiler import PromptCompileInput, compile_video_plan
from libtv_agent.services.shot_expression import plan_shot_expression
from libtv_agent.services.shot_motion import plan_shot_motion
from libtv_agent.templates.facial_expressions import EXPRESSION_LIBRARY, FaceEvidence
from libtv_agent.templates.motion_actions import ACTION_LIBRARY, ActionContext
from libtv_agent.templates.site_style_profiles import get_site_style_profile
from libtv_agent.templates.video_templates import get_video_template
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FACE_CONTEXT = ActionContext.from_values(
    confirmed=(
        "standing_pose",
        "face_visible",
        "face_gaze_adjustable",
    )
)


def visual_facts(asset_id="one", mouth="neutral", *, visible=True, confidence="high"):
    return ImageMotionFacts(
        asset_id=asset_id,
        observations=[
            {
                "capability": "face_visible",
                "state": "visible" if visible else "absent",
                "confidence": "high",
                "evidence": "Directly observed",
            }
        ],
        facial_observation=FacialObservation(
            mouth=mouth,
            eyes_visible=True,
            confidence=confidence,
            evidence="Visible mouth and eyes",
        ),
    )


@pytest.mark.parametrize("visible,confidence", [(False, "high"), (True, "uncertain")])
def test_unreliable_facial_observations_never_enable_expression_changes(visible, confidence):
    assert visual_facts(visible=visible, confidence=confidence).face_evidence() == FaceEvidence()


@pytest.mark.parametrize("mouth", ["unknown", "soft_smile", "open_or_expressive"])
def test_reference_mouth_is_preserved_instead_of_forcing_brand_smile(mouth):
    text, metadata = plan_shot_expression(
        context=FACE_CONTEXT,
        face=FaceEvidence(mouth=mouth, eyes_visible=True),
        pool=("会意一笑",),
        duration=8,
        remaining=5,
        variant_index=1,
        occupied_by_motion=False,
    )
    assert metadata["selectedExpressionId"] == "preserve_source"
    assert metadata["expressionChange"] is False
    assert "保留参考图原有神态" in text
    assert "允许自然眨眼，不规定次数" in text


def test_invisible_face_and_closed_or_hidden_eyes_do_not_get_new_facial_details():
    text, metadata = plan_shot_expression(
        context=ActionContext(),
        face=FaceEvidence(mouth="neutral", eyes_visible=True),
        pool=("自然浅笑",),
        duration=8,
        remaining=5,
        variant_index=1,
        occupied_by_motion=False,
    )
    assert text == ""
    assert not metadata["expressionChange"]
    text, metadata = plan_shot_expression(
        context=FACE_CONTEXT,
        face=FaceEvidence(),
        pool=("自然浅笑",),
        duration=8,
        remaining=5,
        variant_index=1,
        occupied_by_motion=False,
    )
    assert "眨眼" not in text
    assert not metadata["naturalBlinkAllowed"]


@pytest.mark.parametrize("brand", ["nerdywave", "geeksoutfit", "pagewings"])
def test_brand_pool_has_bounded_source_compatible_choices(brand):
    profile = replace(
        get_site_style_profile(brand), primary_motion_pool=("stance_shift",), micro_motion_pool=()
    )
    ids = set()
    for variant in range(1, 5):
        text, metadata = plan_shot_motion(
            profile=profile,
            template_id="mood_model",
            context=FACE_CONTEXT,
            duration=8,
            variant_index=variant,
            library=ACTION_LIBRARY,
            face_evidence=FaceEvidence(mouth="neutral", eyes_visible=True),
            template_motion_pool=(),
            template_detail_pool=(),
        )
        ids.add(metadata["selectedExpressionId"])
        assert metadata["selectedMicroActionId"] is None
        assert metadata["sourceMouthState"] == "neutral"
        assert "表情：" in text
    assert len(ids) >= 3


@pytest.mark.parametrize("duration,occupied", [(1, False), (8, True)])
def test_short_or_already_expressive_shot_does_not_add_another_change(duration, occupied):
    _, metadata = plan_shot_expression(
        context=FACE_CONTEXT,
        face=FaceEvidence(mouth="neutral"),
        pool=("自然浅笑",),
        duration=duration,
        remaining=0,
        variant_index=1,
        occupied_by_motion=occupied,
    )
    assert not metadata["expressionChange"]
    assert metadata["selectedExpressionId"] == "preserve_source"


def test_smile_action_respects_source_and_does_not_restart_an_existing_smile():
    profile = replace(
        get_site_style_profile("pagewings"),
        primary_motion_pool=("eye_contact_soft_smile",),
        micro_motion_pool=(),
    )

    def plan(face):
        return plan_shot_motion(
            profile=profile,
            template_id="mood_model",
            context=FACE_CONTEXT,
            duration=8,
            variant_index=1,
            library=ACTION_LIBRARY,
            face_evidence=face,
            template_motion_pool=(),
            template_detail_pool=(),
        )

    assert plan(FaceEvidence())[1]["fallback"]
    text, selected = plan(FaceEvidence(mouth="soft_smile"))
    assert selected["selectedPrimaryActionId"] == "eye_contact_soft_smile"
    assert "保持参考图已有的轻微微笑" in text
    assert "从自然中性表情开始" not in text
    assert not selected["expressionChange"]


def test_one_small_smile_takes_the_slot_instead_of_adding_another_gaze_reaction():
    profile = replace(
        get_site_style_profile("pagewings"),
        expression_pool=("自然浅笑",),
        primary_motion_pool=("stance_shift",),
        micro_motion_pool=("controlled_gaze",),
    )
    _, selected = plan_shot_motion(
        profile=profile,
        template_id="mood_model",
        context=FACE_CONTEXT,
        duration=8,
        variant_index=1,
        library=ACTION_LIBRARY,
        face_evidence=FaceEvidence(mouth="neutral"),
        template_motion_pool=(),
        template_detail_pool=(),
    )
    assert selected["expressionChange"]
    assert selected["selectedMicroActionId"] is None


@pytest.mark.parametrize("mouth", ["neutral", "soft_smile"])
@pytest.mark.parametrize("as_micro", [False, True])
@pytest.mark.parametrize("as_brand_action", [False, True])
def test_smile_motion_and_brand_derivative_share_one_source_compatible_change(
    mouth, as_micro, as_brand_action
):
    library = dict(ACTION_LIBRARY)
    action_id = "eye_contact_soft_smile"
    if as_brand_action:
        action_id = "brand_soft_smile"
        library[action_id] = replace(
            ACTION_LIBRARY["eye_contact_soft_smile"],
            id=action_id,
            base_action_id="eye_contact_soft_smile",
            instruction="从中性开始微笑，然后恢复中性，再次微笑。",
        )
    profile = replace(
        get_site_style_profile("pagewings"),
        primary_motion_pool=("stance_shift",) if as_micro else (action_id,),
        micro_motion_pool=(action_id,) if as_micro else (),
        expression_pool=("放松从容",),
    )
    text, selected = plan_shot_motion(
        profile=profile,
        template_id="mood_model",
        context=FACE_CONTEXT,
        duration=8,
        variant_index=1,
        library=library,
        face_evidence=FaceEvidence(mouth=mouth, eyes_visible=True),
        template_motion_pool=(),
        template_detail_pool=(),
    )
    assert action_id in selected["selectedActionIds"]
    assert selected["expressionChange"] is (mouth == "neutral")
    assert "从中性开始" not in text
    assert "恢复放松表情" not in text
    assert "再次微笑" not in text
    if mouth == "neutral":
        assert text.count("嘴角轻微上扬一次") == 1
        assert "保留参考图原有神态、嘴角和口型" not in text
    else:
        assert "保持参考图已有的轻微微笑" in text
        assert "嘴角轻微上扬一次" not in text


def test_unknown_catalog_expression_is_not_injected_as_an_instruction():
    text, selected = plan_shot_expression(
        context=FACE_CONTEXT,
        face=FaceEvidence(mouth="neutral"),
        pool=("换脸、露齿大笑并眨单眼",),
        duration=8,
        remaining=5,
        variant_index=1,
        occupied_by_motion=False,
    )
    assert "换脸" not in text
    assert not selected["expressionChange"]


def test_visible_faces_across_maximum_reference_counts_fit_provider_prompt_budget():
    from libtv_agent.services.fixed_video_tasks import _reference_role_prompt

    for brand in ("nerdywave", "geeksoutfit", "pagewings"):
        for count in range(1, 8):
            for duration in (3, 15):
                for variant in range(1, 5):
                    roles = _reference_role_prompt(
                        storyboard_count=count, subject_image_count=0, element_count=0
                    )
                    compiled = compile_video_plan(
                        PromptCompileInput(
                            template=get_video_template("ootd_walk"),
                            storyboard_count=count,
                            result_count=4,
                            variant_index=variant,
                            duration_seconds=duration,
                            action_intensity="standard",
                            camera_tempo="standard",
                            cut_mode="explicit_cuts",
                            site_profile=get_site_style_profile(brand),
                            shot_action_contexts=(FACE_CONTEXT,) * count,
                            shot_face_evidence=(FaceEvidence("neutral", True),) * count,
                            prompt_budget=3072 - len(roles) - 1,
                        )
                    )
                    assert len(roles + " " + compiled.master_prompt) <= 3072


def test_expression_facts_are_per_image_and_survive_prompt_compression():
    profile = replace(
        get_site_style_profile("geeksoutfit"),
        expression_pool=("会意一笑",),
        primary_motion_pool=("stance_shift",),
        micro_motion_pool=(),
    )
    compiled = compile_video_plan(
        PromptCompileInput(
            template=replace(
                get_video_template("mood_model"),
                motion_action_pool=(),
                detail_motion_pool=(),
            ),
            storyboard_count=2,
            result_count=1,
            variant_index=1,
            duration_seconds=10,
            action_intensity="standard",
            camera_tempo="standard",
            cut_mode="explicit_cuts",
            site_profile=profile,
            shot_action_contexts=(FACE_CONTEXT, FACE_CONTEXT),
            shot_face_evidence=(
                FaceEvidence(mouth="neutral"),
                FaceEvidence(mouth="open_or_expressive"),
            ),
            learning_guidance="自然展示。" * 300,
            prompt_budget=1700,
        )
    )
    selections = compiled.variant_seed["shotMotionSelections"]
    assert selections[0]["expressionChange"]
    assert not selections[1]["expressionChange"]
    assert "嘴角轻微扬起一次" in compiled.shot_instructions[0].instruction
    assert "保留参考图原有神态" in compiled.shot_instructions[1].instruction
    assert compiled.shot_instructions[0].instruction in compiled.master_prompt
    assert compiled.shot_instructions[1].instruction in compiled.master_prompt
    assert len(compiled.master_prompt) <= 1700


async def test_facial_facts_round_trip_through_the_existing_cache(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[VisualFactCache.__table__])
        )
    monkeypatch.setattr(automatic_motion_facts, "paid_generation_authorized", lambda _: True)

    async def analyze(images):
        return MotionFactsResult(images=[visual_facts(images[0].asset_id, "soft_smile")])

    analyzer = AsyncMock(side_effect=analyze)
    monkeypatch.setattr(
        automatic_motion_facts,
        "OpenAIMotionFactsAdapter",
        lambda _: SimpleNamespace(analyze=analyzer),
    )
    Image.new("RGB", (128, 128)).save(tmp_path / "one.jpg")
    settings = Settings(
        ai_adapter_mode="openai",
        openai_api_key="synthetic-test-key",
        artifact_root=tmp_path,
        app_env="test",
        _env_file=None,
    )
    upload = StagedUpload(id=uuid.uuid4(), sha256="a" * 64, object_key="one.jpg")
    user = uuid.uuid4()
    async with async_sessionmaker(engine)() as session:

        async def observe():
            return (
                await observe_storyboards(
                    session,
                    uploads=[upload],
                    user_id=user,
                    brand_id=None,
                    settings=settings,
                    object_store=LocalObjectStore(tmp_path),
                )
            )[0]

        first, cached = await observe(), await observe()
        assert first.face_evidence == cached.face_evidence == FaceEvidence("soft_smile", True)
        assert cached.source == "automatic_vision_cache"
        assert analyzer.await_count == 1
    await engine.dispose()


async def test_fixed_task_compiles_server_face_facts_without_manual_input(tmp_path, monkeypatch):
    async def observe(session, **kwargs):
        return [
            ObservedMotion(
                FACE_CONTEXT, "automatic_vision", face_evidence=FaceEvidence("soft_smile", True)
            )
        ]

    monkeypatch.setattr(fixed_video_tasks, "observe_storyboards", observe)
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        object_store_mode="local",
        artifact_root=tmp_path,
        runner_root=tmp_path / "runner",
        kling_admin_config_file=tmp_path / "config.json",
        _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = io.BytesIO()
            Image.new("RGB", (360, 640)).save(payload, format="PNG")
            headers = {"X-User-Id": settings.dev_user_id, "Idempotency-Key": "expression-image"}
            uploaded = await client.post(
                "/api/v1/uploads",
                headers=headers,
                files={"file": ("image.png", payload.getvalue(), "image/png")},
            )
            assert uploaded.status_code == 201
            headers["Idempotency-Key"] = "expression-task"
            task = await client.post(
                "/api/v1/video-tasks",
                headers=headers,
                json={
                    "clientTaskId": "expression-task",
                    "title": "Face test",
                    "storyboards": [{"uploadId": uploaded.json()["id"], "order": 1}],
                    "templateId": "ootd_walk",
                    "siteProfileId": "geeksoutfit",
                    "resultCount": 1,
                    "durationSeconds": 8,
                    "paidSubmissionConsent": True,
                },
            )
            assert task.status_code == 201, task.text
            result = task.json()
            selected = result["compiledPlan"]["variants"][0]["variant_seed"][
                "shotMotionSelections"
            ][0]
            assert selected["selectedExpressionId"] == "preserve_source"
            assert not selected["expressionChange"]
            assert selected["sourceMouthState"] == "soft_smile"
            assert (
                result["materialValidation"]["storyboardActionFacts"][0]["faceEvidence"]["mouth"]
                == "soft_smile"
            )
            assert result["materialValidation"]["visionCheckRequired"] is False


def test_only_small_observable_expression_presets_are_absorbed():
    assert len(EXPRESSION_LIBRARY) == 7
    assert all(item.id for item in EXPRESSION_LIBRARY.values())
    assert get_site_style_profile("goodnurselife").status == "active"
