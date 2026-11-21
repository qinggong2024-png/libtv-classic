"""AI storyboard-driven video prompt integration and guarded offline simulation."""

import base64
import io
import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.app import create_app
from libtv_agent.config import Settings
from libtv_agent.domain.models import Base
from libtv_agent.services import fixed_video_tasks
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_PLANNER_VERSION,
    CreativeDirectorResponse,
    CreativePlannerError,
    PlannedBeat,
    PlannedVariant,
    PlannedVariantBatch,
    VisibleReferencePerson,
    VisibleReferenceProp,
    VisualReferenceResult,
    VisualStoryboardFact,
    _optimized_storyboard_image_url,
    compile_creative_variants,
    plan_creative_video,
    planner_timeout_seconds,
)
from libtv_agent.services.async_video_submission import (
    pending_automatic_video_ids,
    process_automatic_video_submission,
)
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from libtv_agent.templates.video_templates import VIDEO_TEMPLATES
from test_fixed_template_foundation import png_bytes


def fixture_solo_facts():
    # These facts are declared test evidence, not the result of real image recognition.
    return VisualReferenceResult(
        storyboards=[
            VisualStoryboardFact(
                frame_order=1,
                person_count_verified=True,
                people=[VisibleReferencePerson(
                    person_id="p1", visible_evidence="One adult woman, left of shelf"
                )],
                props=[VisibleReferenceProp(
                    prop_id="book", visible_evidence="Open paperback held in left hand"
                )],
                scene="small independent bookstore, shelves on viewer's right",
                initial_pose="standing with an open book in left hand",
                framing="eye-level medium shot, handheld",
                lighting="soft daylight from left window",
                garment_evidence=(
                    "original artwork of a small reading ghost, partly visible above the book"
                ),
                spatial_constraints="narrow aisle; no safe path for walking forward",
            )
        ],
        product_appearance=(
            "Crewneck sweatshirt with a small reading ghost graphic; preserve exact artwork"
        ),
    )


def fixture_solo_plan():
    # Offline visual evidence is synthetic, but the creative beats go through the
    # exact same validator/compiler used by the video task service.
    return PlannedVariantBatch(variants=[PlannedVariant(
        creative_idea="A real reader and her sweater's bookish ghost tell one visual joke",
        visual_hook="The open book lowers and reveals a little reading ghost on the sweatshirt",
        buyer_reason=(
            "Readers can wear their personality through the specific reading-ghost graphic"
        ),
        beats=[
            PlannedBeat(
                frame_order=1, shot_role="hook", transition="start",
                duration_weight=1.7, purpose="Instant book-to-shirt visual reveal",
                entry_state="Woman holds her already open paperback across lower chest",
                action="She lowers the open paperback in one natural brisk movement",
                exit_state="Open book stays in left hand, sweatshirt print becomes clear",
                camera="Medium eye-level handheld view, immediate garment-centered motion",
                product_proof="Reveal the reading-ghost motif already visible on the sweatshirt",
                actor_ids=["p1"], interaction_partner_ids=[], used_prop_ids=["book"],
            ),
            PlannedBeat(
                frame_order=1, shot_role="reveal", transition="cut",
                duration_weight=1.3, purpose="Make the design readable before three seconds",
                entry_state="Book stays low and clear of the chest",
                action="Her breathing shifts the garment fabric naturally",
                exit_state="Graphic remains fully unobstructed",
                camera="Quick cut to a close crop of the already visible front graphic",
                product_proof="Clear reading-ghost artwork, no added or redesigned typography",
                actor_ids=["p1"], interaction_partner_ids=[], used_prop_ids=["book"],
            ),
            PlannedBeat(
                frame_order=1, shot_role="proof", transition="cut",
                duration_weight=3.2, purpose="Connect the shirt to a reader's personality",
                entry_state="Book open in left hand, body stays by the same shelf",
                action="She looks up with a brief private knowing grin then back to the page",
                exit_state="Relaxed shoulders and book held naturally",
                camera="Cut back to an upper-body shot, retain right-side bookshelf",
                product_proof="Graphic remains visible on the real worn garment",
                actor_ids=["p1"], interaction_partner_ids=[], used_prop_ids=["book"],
            ),
            PlannedBeat(
                frame_order=1, shot_role="closing", transition="cut",
                duration_weight=3.8, purpose="Finish with identity and clear outfit",
                entry_state="Still standing at the same spot holding the open paperback",
                action="She turns one page and resumes reading without posing",
                exit_state="Settles into a believable reading moment",
                camera="Cut to a slightly wider medium crop, same eye-level source view",
                product_proof="Keep front graphic and natural fit readable in final frame",
                actor_ids=["p1"], interaction_partner_ids=[], used_prop_ids=["book"],
            ),
        ],
    )])

def compile_fixture():
    return compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=fixture_solo_plan(),
        storyboard_count=1,
        variant_count=1,
        duration_seconds=10,
        reference_role_prompt=(
            "References: @image_1 is the ONLY source storyboard for people, "
            "scene, composition and starting pose."
        ),
        brand_tone="PageWings: real readers, everyday bright approachable UGC",
        creative_brief="Show a reader's identity while keeping the product readable",
    )[0]


def test_real_compiler_offline_simulation_emits_full_reference_bound_prompt():
    compiled = compile_fixture()
    print("\n=== OFFLINE COMPILED PROMPT ===\n" + compiled.prompt)
    assert compiled.planner_version == CREATIVE_PLANNER_VERSION
    assert "References: @image_1" in compiled.prompt
    assert "[@image_1, HOOK, START]" in compiled.prompt
    assert "[@image_1, REVEAL, CUT]" in compiled.prompt
    assert "[@image_1, PROOF, CUT]" in compiled.prompt
    assert "[@image_1, CLOSING, CUT]" in compiled.prompt
    assert "Buyer reason: Readers can wear their personality" in compiled.prompt
    assert len(compiled.beats) == 4
    assert "paperback" in compiled.prompt
    assert "do not introduce new people" in compiled.prompt.lower()
    assert "three_quarter_turn" not in compiled.prompt
    assert "模特展示衣服细节" not in compiled.prompt
    assert len(compiled.prompt) <= 3072


def test_product_reference_controls_artwork_and_surface_without_verbose_restating():
    facts = fixture_solo_facts()
    plan = fixture_solo_plan()
    compiled = compile_creative_variants(
        observations=facts,
        batch=plan,
        storyboard_count=1,
        variant_count=1,
        duration_seconds=10,
        reference_role_prompt=(
            "References: @image_1 anchors person and scene. "
            "@image_2 is the primary product reference."
        ),
        brand_tone="Natural offbeat everyday UGC",
    )[0]
    assert "Garment: match @image_2 exactly" in compiled.prompt
    assert "every printed word, color and placement" in compiled.prompt
    assert "Product: Crewneck sweatshirt with a small reading ghost graphic" not in compiled.prompt
    assert "reading-ghost" in compiled.prompt  # Model-generated shot evidence survives.
    assert "Creative tone: Natural offbeat everyday UGC" in compiled.prompt
    assert len(compiled.prompt) <= 3072


def test_ai_compiler_includes_matched_sweatshirt_material_without_rewriting_print():
    compiled = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=fixture_solo_plan(),
        storyboard_count=1,
        variant_count=1,
        duration_seconds=10,
        reference_role_prompt=(
            "References: @image_1 anchors person and scene. "
            "@image_2 is the primary product reference."
        ),
        garment_material="sweatshirt",
    )[0]
    assert "Material look (only if consistent with product image)" in compiled.prompt
    assert "ordinary soft sweatshirt knit" in compiled.prompt
    assert "Garment: match @image_2 exactly" in compiled.prompt
    assert "Product: Crewneck sweatshirt" not in compiled.prompt
    assert "mohair-style fibers" not in compiled.prompt
    assert len(compiled.prompt) <= 3072


def test_ai_compiler_leaves_material_undefined_if_not_confirmed():
    compiled = compile_fixture()
    assert "Material look (only if consistent" not in compiled.prompt


def test_mirror_selfie_model_prompt_requires_hand_and_camera_continuity():
    from libtv_agent.services.ai_creative_planner import CREATIVE_SYSTEM_PROMPT

    assert "same hand throughout" in CREATIVE_SYSTEM_PROMPT
    assert "captured mirror framing" in CREATIVE_SYSTEM_PROMPT
    assert "cannot freely spread both arms" in CREATIVE_SYSTEM_PROMPT
    assert "never" in CREATIVE_SYSTEM_PROMPT.lower()


def test_verbose_grounded_shot_plan_compacts_to_provider_limit():
    """Preserve shot count and visible product proof even when model uses long fields."""
    facts = fixture_solo_facts()
    original = fixture_solo_plan().variants[0]
    beats = [
        beat.model_copy(update={
            "action": (beat.action + " while keeping the pose and worn garment visible").strip(),
            "camera": (beat.camera + " with natural handheld sway and strong visual movement").strip(),
            "product_proof": (beat.product_proof + "; original colors and print remain legible").strip(),
            "purpose": (beat.purpose + " while emphasizing the actual garment").strip(),
        })
        for beat in original.beats
    ]
    batch = PlannedVariantBatch(variants=[original.model_copy(update={"beats": beats})])
    result = compile_creative_variants(
        observations=facts, batch=batch,
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt=(
            "References: @image_1 is the source image controlling identity, product, "
            "scene, lighting, composition, and the starting pose. The visible pose is a "
            "starting anchor; execute the planned action progression and do not freeze "
            "the video at that pose. @image_2 is the primary product reference. Product "
            "references guide the product's visible appearance, style, print, logo, and "
            "visible text only; do not use them to replace the people, scene, composition, "
            "starting pose, planned action, or camera language from the source images."
        ),
    )[0]
    assert len(result.prompt) <= 3072
    assert len(result.beats) == 4
    assert "Buyer reason:" in result.prompt
    assert "Product hook:" in result.prompt
    assert "[@image_1, HOOK, START]" in result.prompt
    assert "[@image_1, CLOSING, CUT]" in result.prompt
    # Compact prompts must retain the entry/exit state of every beat, not
    # reduce a complex action to an unmotivated pose or disconnected movement.
    assert result.prompt.count("Start: ") == len(result.beats)
    assert result.prompt.count("End: ") == len(result.beats)
    assert "Action: She turns one page" in result.prompt
    assert "reading-ghost" in result.prompt


@pytest.mark.parametrize(
    "bad_action",
    [
        "A friend laughs at her sweatshirt",
        "She turns to him with a surprised look",
        "Another hand comes into the frame",
        "朋友走过来看到图案惊讶",
    ],
)
def test_single_person_reference_cannot_acquire_an_extra_actor(bad_action):
    original = fixture_solo_plan().variants[0]
    invalid_beat = original.beats[0].model_copy(update={"action": bad_action})
    batch = PlannedVariantBatch(variants=[
        original.model_copy(update={"beats": [invalid_beat, original.beats[1]]}),
    ])
    with pytest.raises(CreativePlannerError, match="solo|unverified"):
        compile_creative_variants(
            observations=fixture_solo_facts(), batch=batch,
            storyboard_count=1, variant_count=1, duration_seconds=10,
            reference_role_prompt="Reference @image_1",
        )


def test_uncertain_person_count_does_not_block_known_actor_motion():
    facts = fixture_solo_facts()
    facts.storyboards[0].person_count_verified = False
    compiled = compile_creative_variants(
        observations=facts, batch=fixture_solo_plan(),
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt="Reference @image_1",
    )
    assert len(compiled[0].beats) == 4
    assert compiled[0].facts[0]["person_count_verified"] is False


def _two_frame_fixture(*, linked: bool, evidence: bool = True):
    first = fixture_solo_facts().storyboards[0]
    person1 = first.people[0].model_copy(update={
        "character_key": "reader-main" if linked else None,
        "match_evidence": "same hairstyle and graphic sweatshirt" if linked and evidence else "",
    })
    second_person = person1.model_copy(update={
        "person_id": "person-in-second-image",
        "visible_evidence": "Adult woman with the same outfit in the aisle",
    })
    frames = [
        first.model_copy(update={"people": [person1]}),
        first.model_copy(update={
            "frame_order": 2, "people": [second_person],
            "initial_pose": "standing near the same shelf",
        }),
    ]
    plan = fixture_solo_plan().variants[0]
    beats = [
        plan.beats[0],
        plan.beats[1].model_copy(update={
            "frame_order": 2, "actor_ids": ["person-in-second-image"],
            "transition": "reframe",
        }),
    ]
    return (
        VisualReferenceResult(
            storyboards=frames, product_appearance=fixture_solo_facts().product_appearance
        ),
        PlannedVariantBatch(variants=[plan.model_copy(update={"beats": beats})]),
    )


def test_cross_frame_continuity_requires_visual_identity_link():
    facts, batch = _two_frame_fixture(linked=False)
    with pytest.raises(CreativePlannerError, match="identity link"):
        compile_creative_variants(
            observations=facts, batch=batch, storyboard_count=2,
            variant_count=1, duration_seconds=10, reference_role_prompt="@image_1 @image_2",
        )


def test_cross_frame_identity_link_with_evidence_allows_continuity():
    facts, batch = _two_frame_fixture(linked=True)
    compiled = compile_creative_variants(
        observations=facts, batch=batch, storyboard_count=2, variant_count=1,
        duration_seconds=10, reference_role_prompt="@image_1 @image_2",
    )[0]
    assert "reader-main" in compiled.prompt
    assert "person-in-second-image=reader-main" in compiled.prompt


def test_cross_frame_identity_without_matching_evidence_is_rejected():
    facts, batch = _two_frame_fixture(linked=True, evidence=False)
    with pytest.raises(CreativePlannerError, match="match evidence"):
        compile_creative_variants(
            observations=facts, batch=batch, storyboard_count=2,
            variant_count=1, duration_seconds=10, reference_role_prompt="@image_1 @image_2",
        )


def test_distinct_people_across_frames_can_be_used_with_explicit_cut():
    facts, batch = _two_frame_fixture(linked=False)
    original = batch.variants[0]
    second = original.beats[1].model_copy(update={"transition": "cut"})
    batch = PlannedVariantBatch(variants=[
        original.model_copy(update={"beats": [original.beats[0], second]})
    ])
    compiled = compile_creative_variants(
        observations=facts, batch=batch, storyboard_count=2,
        variant_count=1, duration_seconds=10, reference_role_prompt="@image_1 @image_2",
    )[0]
    assert "unlinked" in compiled.prompt


def test_multimodal_timeout_budget_uses_configured_analysis_deadline():
    settings = Settings(app_env="test", _env_file=None)
    assert planner_timeout_seconds(settings, 2) == 180.0
    assert planner_timeout_seconds(settings, 7) == 225.0
    faster = Settings(app_env="test", openai_analysis_timeout_seconds=85.0,
                      _env_file=None)
    assert planner_timeout_seconds(faster, 2) == 85.0


def test_storyboard_analysis_image_downsamples_without_changing_product_reference():
    from PIL import Image

    image_buffer = io.BytesIO()
    Image.new("RGB", (1536, 2752), color=(125, 111, 103)).save(
        image_buffer, format="JPEG", quality=94,
    )
    original = "data:image/jpeg;base64," + base64.b64encode(image_buffer.getvalue()).decode()
    optimized = _optimized_storyboard_image_url(original)
    with Image.open(io.BytesIO(base64.b64decode(optimized.split(",", 1)[1]))) as picture:
        assert max(picture.size) <= 1600
        assert picture.height > picture.width
    assert len(optimized) < len(original)
    assert _optimized_storyboard_image_url("data:image/png;base64,AAA") == "data:image/png;base64,AAA"
    assert _optimized_storyboard_image_url("https://example.org/storyboard.jpg") == "https://example.org/storyboard.jpg"


class FakeResponses:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=CreativeDirectorResponse(
            observations=fixture_solo_facts(),
            variants=fixture_solo_plan().variants,
        ))


@pytest.mark.asyncio
async def test_single_multimodal_planning_call_includes_images_and_product_reference():
    response = FakeResponses()
    settings = Settings(app_env="test", openai_api_key=None, _env_file=None)
    output = await plan_creative_video(
        settings=settings,
        storyboard_images=[AnalysisImageInput("frame-one", 1, "data:image/png;base64,AAA")],
        product_images=[AnalysisImageInput("product-one", 1, "data:image/png;base64,BBB")],
        reference_role_prompt="References: @image_1 is the source.",
        creative_brief="Show the garment naturally",
        brand_tone="PageWings",
        duration_seconds=10, variant_count=1,
        target_audience="real adult readers",
        product_priorities=("show exact front graphic", "natural fit"),
        forbidden_mismatches=("no invented garment writing",),
        client=SimpleNamespace(responses=response),
    )
    assert len(response.calls) == 1
    request = json.loads(response.calls[0]["input"][1]["content"][0]["text"])
    assert request["targetAudience"] == "real adult readers"
    assert request["productPriorities"] == ["show exact front graphic", "natural fit"]
    assert request["forbiddenMismatches"] == ["no invented garment writing"]
    assert response.calls[0]["text_format"] is CreativeDirectorResponse
    images = [
        item for item in response.calls[0]["input"][1]["content"]
        if item["type"] == "input_image"
    ]
    assert [image["image_url"] for image in images] == [
        "data:image/png;base64,AAA", "data:image/png;base64,BBB",
    ]
    assert "Improvise credible movement" in output[0].prompt


@pytest.mark.asyncio
async def test_video_task_creation_uses_ai_compiled_prompt_without_video_provider(
    tmp_path, monkeypatch,
):
    monkeypatch.setitem(VIDEO_TEMPLATES, "ootd_walk", replace(
        VIDEO_TEMPLATES["ootd_walk"], status="active",
    ))
    monkeypatch.setitem(SITE_STYLE_PROFILES, "pagewings", replace(
        SITE_STYLE_PROFILES["pagewings"], status="active",
    ))
    seen = []

    async def mocked_planner(**kwargs):
        seen.append(kwargs)
        return (compile_fixture(),)

    monkeypatch.setattr(fixed_video_tasks, "plan_creative_video", mocked_planner)
    settings = Settings(
        app_env="test", database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}",
        artifact_root=tmp_path / "artifacts", object_store_mode="local",
        runner_root=tmp_path / "runner", kling_admin_config_file=tmp_path / "kling.json",
        ai_adapter_mode="openai", openai_api_key="fake-for-test-only",
        kling_cli_enabled=False, _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            upload = await client.post(
                "/api/v1/uploads",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "ai-upload"},
                files={"file": ("solo.png", png_bytes(), "image/png")},
            )
            assert upload.status_code == 201, upload.text
            response = await client.post(
                "/api/v1/video-tasks",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "ai-task"},
                json={
                    "clientTaskId": "ai-task-1", "title": "Reader UGC",
                    "storyboards": [{"uploadId": upload.json()["id"], "order": 1}],
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10,
                    "resultCount": 1, "paidSubmissionConsent": True,
                },
            )
            assert response.status_code == 201, response.text
            accepted = response.json()
            assert accepted["phase"] == "ANALYZING"
            assert accepted["analysisStage"] == "QUEUED"
            assert not seen, "HTTP acceptance must not wait for the AI provider"
            async with app.state.database.session_factory() as queued_session:
                assert uuid.UUID(accepted["id"]) in await pending_automatic_video_ids(queued_session)
            replay = await client.post(
                "/api/v1/video-tasks",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "ai-task"},
                json={
                    "clientTaskId": "ai-task-1", "title": "Reader UGC",
                    "storyboards": [{"uploadId": upload.json()["id"], "order": 1}],
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10,
                    "resultCount": 1, "paidSubmissionConsent": True,
                },
            )
            assert replay.status_code == 201
            assert replay.json()["id"] == accepted["id"]
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, uuid.UUID(accepted["id"]),
            )
            refreshed = await client.get(
                f'/api/v1/tasks/{accepted["id"]}',
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert refreshed.status_code == 200
            result = refreshed.json()
            assert seen and len(seen[0]["storyboard_images"]) == 1
            assert seen[0]["cut_mode"] == "explicit_cuts"
            assert seen[0]["action_intensity"] == "standard"
            assert seen[0]["target_audience"] == SITE_STYLE_PROFILES["pagewings"].audience
            profile = SITE_STYLE_PROFILES["pagewings"]
            assert seen[0]["product_priorities"] == profile.product_priorities
            assert seen[0]["forbidden_mismatches"] == profile.forbidden_mismatches
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, uuid.UUID(accepted["id"]),
            )
            assert len(seen) == 1, "A previously claimed task must never run twice"
            assert result["compiledPlan"]["compiler"] == CREATIVE_PLANNER_VERSION
            assert result["variants"][0]["promptText"] == compile_fixture().prompt
            assert result["compiledPlan"]["variants"][0]["creative_idea"]
            assert result["materialValidation"]["aiCreativePlanner"]["verified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_code,http_status", [
    ("AI_CREATIVE_PLAN_INVALID", 422),
    ("AI_CREATIVE_GATEWAY_TIMEOUT", 504),
])
async def test_video_task_creation_blocks_bad_ai_plan_before_task_is_created(
    tmp_path, monkeypatch, failure_code, http_status,
):
    async def blocked(**kwargs):
        raise CreativePlannerError(
            "upstream or validation failure", code=failure_code,
            upstream_status=504 if http_status == 504 else None,
            validation_rule=(
                "NATIVE_HARD_CUT_COUNT"
                if failure_code == "AI_CREATIVE_PLAN_INVALID" else None
            ),
        )
    monkeypatch.setattr(fixed_video_tasks, "plan_creative_video", blocked)
    settings = Settings(
        app_env="test", database_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}",
        artifact_root=tmp_path / "artifacts", object_store_mode="local",
        runner_root=tmp_path / "runner", kling_admin_config_file=tmp_path / "kling.json",
        ai_adapter_mode="openai", openai_api_key="fake-for-test-only",
        kling_cli_enabled=False, _env_file=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            upload = await client.post(
                "/api/v1/uploads",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "blocked-upload"},
                files={"file": ("solo.png", png_bytes(), "image/png")},
            )
            result = await client.post(
                "/api/v1/video-tasks",
                headers={"X-User-Id": settings.dev_user_id, "Idempotency-Key": "blocked-plan"},
                json={
                    "clientTaskId": "blocked-ai-plan", "title": "No reference match",
                    "storyboards": [{"uploadId": upload.json()["id"], "order": 1}],
                    "generationMode": "automatic", "templateId": "ootd_walk",
                    "siteProfileId": "pagewings", "durationSeconds": 10,
                    "resultCount": 1, "paidSubmissionConsent": True,
                },
            )
            assert result.status_code == 201, result.text
            accepted = result.json()
            assert accepted["phase"] == "ANALYZING"
            await process_automatic_video_submission(
                app.state.database, settings, app.state.kling_adapter,
                app.state.object_store, uuid.UUID(accepted["id"]),
            )
            refreshed = await client.get(
                f'/api/v1/tasks/{accepted["id"]}',
                headers={"X-User-Id": settings.dev_user_id},
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["phase"] == "ACTION_REQUIRED"
            assert refreshed.json()["phaseReasonCode"] == failure_code
            assert refreshed.json()["variants"] == [], "AI failure must not submit paid video"
            diagnostic = (refreshed.json().get("materialValidation") or {}).get(
                "aiCreativePlanner", {}
            )
            if failure_code == "AI_CREATIVE_PLAN_INVALID":
                assert diagnostic == {
                    "status": "REJECTED",
                    "validationRule": "NATIVE_HARD_CUT_COUNT",
                    "requestAttempts": 1,
                }
            else:
                assert not diagnostic

def test_raw_unverified_user_brief_is_not_pasted_into_paid_provider_prompt():
    result = compile_creative_variants(
        observations=fixture_solo_facts(), batch=fixture_solo_plan(),
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt="Only @image_1 controls scene and characters",
        creative_brief="MAKE A NEW FRIEND ENTER FROM OUTSIDE AND REACT TO THE OUTFIT",
    )[0]
    assert "MAKE A NEW FRIEND ENTER" not in result.prompt


def test_two_shot_plan_is_allowed_without_a_creative_gate():
    original = fixture_solo_plan().variants[0]
    shortened = original.model_copy(update={"beats": [original.beats[0], original.beats[-1]]})
    result = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=PlannedVariantBatch(variants=[shortened]),
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt="Only @image_1",
    )
    assert len(result[0].beats) == 2


def test_hook_timing_is_director_guidance_not_a_submission_gate():
    original = fixture_solo_plan().variants[0]
    changed = original.beats[0].model_copy(update={"duration_weight": 5.5})
    result = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=PlannedVariantBatch(variants=[
            original.model_copy(update={"beats": [changed, *original.beats[1:]]}),
        ]),
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt="Only @image_1",
    )
    assert result[0].beats[0]["endSeconds"] > 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("buyer_reason", ""), ("buyer_reason", "looks good"),
        ("visual_hook", " "), ("visual_hook", "show the shirt"),
    ],
)
def test_missing_or_generic_marketing_content_is_rejected(field, value):
    original = fixture_solo_plan().variants[0]
    with pytest.raises(CreativePlannerError, match="Hook|buyer reason"):
        compile_creative_variants(
            observations=fixture_solo_facts(),
            batch=PlannedVariantBatch(variants=[
                original.model_copy(update={field: value}),
            ]),
            storyboard_count=1, variant_count=1, duration_seconds=10,
            reference_role_prompt="Only @image_1",
        )


def test_unseen_friend_in_hook_metadata_is_rejected_for_solo_reference():
    original = fixture_solo_plan().variants[0]
    with pytest.raises(CreativePlannerError, match="solo"):
        compile_creative_variants(
            observations=fixture_solo_facts(),
            batch=PlannedVariantBatch(variants=[
                original.model_copy(update={"visual_hook": "A friend reacts to the shirt"}),
            ]),
            storyboard_count=1, variant_count=1, duration_seconds=10,
            reference_role_prompt="Only @image_1",
        )


def test_continuous_mode_accepts_motivated_reframes_without_forced_cuts():
    original = fixture_solo_plan().variants[0]
    shots = [
        beat.model_copy(update={"transition": "start" if i == 0 else "reframe"})
        for i, beat in enumerate(original.beats)
    ]
    output = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=PlannedVariantBatch(variants=[
            original.model_copy(update={"beats": shots}),
        ]),
        storyboard_count=1, variant_count=1, duration_seconds=10,
        reference_role_prompt="Only @image_1",
        cut_mode="continuous_when_possible",
    )
    assert "REFRAME" in output[0].prompt


def test_product_only_cannot_sneak_human_action_as_untyped_text():
    product = VisualReferenceResult(
        storyboards=[VisualStoryboardFact(
            frame_order=1, person_count_verified=True,
            people=[], props=[], scene="tabletop", initial_pose="none",
            framing="product close-up", lighting="window light",
            garment_evidence="shirt laid flat", spatial_constraints="no person is shown",
        )],
        product_appearance="flat shirt photographed on table",
    )
    plan = PlannedVariantBatch(variants=[PlannedVariant(
        creative_idea="Only product view",
        visual_hook="Reveal the tabletop garment graphic with a close crop",
        buyer_reason="The original printed artwork expresses the wearer\'s taste",
        beats=[PlannedBeat(
            frame_order=1, duration_weight=1,
            purpose="Present the product", entry_state="Shirt is on the table",
            action="She gently adjusts the collar", exit_state="Garment stays centered",
            camera="Close handheld product shot", product_proof="Reference print",
            actor_ids=[], interaction_partner_ids=[], used_prop_ids=[],
        )],
    )])
    with pytest.raises(CreativePlannerError, match="product-only"):
        compile_creative_variants(
            observations=product, batch=plan, storyboard_count=1,
            variant_count=1, duration_seconds=10,
            reference_role_prompt="@image_1 product view",
        )
