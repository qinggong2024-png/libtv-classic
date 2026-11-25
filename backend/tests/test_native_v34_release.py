"""ALLWE Classic 5–15s V3.4 release checks, zero paid Sol/Kling requests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    PlannedVariantBatch,
    compile_creative_variants,
)
from libtv_agent.services.creative_plan_v25 import expand_wire_response
from libtv_agent.services.native_five_shot import native_i_eligible
from libtv_agent.services.native_v34_director import (
    ACTION_SYSTEM_PROMPT,
    CUT_MARKER,
    DIRECTIVE,
    PROFILE,
    compile_native_v34_prompt,
    creative_guidance,
    max_shots_for_duration,
    native_v34_eligible,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan
from test_native_i_release import _compile as old_i_compile
from test_native_i_release import _native_variant, _wire


@pytest.mark.parametrize(
    ("duration", "cap"),
    [
        (5, 3),
        (6, 3),
        (7, 4),
        (8, 4),
        (9, 5),
        (10, 5),
        (11, 5),
        (12, 6),
        (13, 6),
        (14, 6),
        (15, 6),
    ],
)
def test_duration_controls_more_useful_opportunities_not_walk_quota(duration, cap):
    assert max_shots_for_duration(duration) == cap
    guide = creative_guidance(duration)
    assert f"Make a natural {duration}s video with up to {cap}" in guide
    assert "natural-language sequence FIRST" in guide
    assert "CUT TO is useful" in guide
    assert "full-speed" not in guide  # everyday speed is stated plainly
    assert "CUT TO a wide view" not in guide
    assert "camera can move" in guide.lower()



@pytest.mark.asyncio
async def test_v34_causal_product_hook_reaches_only_opt_in_planner(monkeypatch):
    """Capture the real Sol request in memory; no AI or video provider call."""
    import json

    from libtv_agent.adapters.openai_analysis import AnalysisImageInput
    from libtv_agent.services import ai_creative_planner as planner
    from libtv_agent.services.ai_creative_planner import (
        CreativePlannerError,
        plan_creative_video,
    )

    captured_messages = []

    async def capture_and_stop(_settings, messages, _schema, _budget, **_kwargs):
        captured_messages.append(messages)
        raise CreativePlannerError("deliberate offline request capture")

    monkeypatch.setattr(planner, "_parse_provider", capture_and_stop)
    settings = Settings(
        app_env="test",
        _env_file=None,
        ai_adapter_mode="openai",
        openai_model="gpt-6.1-sol",
        openai_api_key="unit-test-only",
    )
    inputs = dict(
        settings=settings,
        storyboard_images=[
            AnalysisImageInput("scene-1", 1, "data:image/png;base64,AAA")
        ],
        product_images=[
            AnalysisImageInput("garment-1", 1, "data:image/png;base64,BBB")
        ],
        reference_role_prompt="References: @image_1 wearer; @image_2 garment.",
        creative_brief="A printed joke is the hook, not an artificial walking scene.",
        brand_tone="dry and playful American UGC",
        duration_seconds=10,
        variant_count=1,
        cut_mode="explicit_cuts",
        client=object(),
        experimental_v25=True,
        marketing_mode="usp",
    )

    for enabled in (True, False):
        with pytest.raises(CreativePlannerError, match="deliberate offline"):
            await plan_creative_video(**inputs, native_v34=enabled)

    assert len(captured_messages) == 2
    native_system = captured_messages[0][0]["content"]
    default_system = captured_messages[1][0]["content"]
    assert "WRITE THE VIDEO_PROMPT FIRST" in native_system
    assert "ONLY AFTER the creative prompt" in native_system
    assert "NATURAL VIDEO DIRECTION" in native_system
    assert "video_prompt is the creative source of truth" in native_system
    assert "Show cause -> actor reaction" not in native_system
    assert "WRITE THE VIDEO_PROMPT FIRST" not in default_system
    assert "Show cause -> actor reaction" in default_system

    native_brief = json.loads(captured_messages[0][1]["content"][0]["text"])
    assert "Make a natural 10s video" in native_brief["creativeDirective"]
    assert native_brief["creativeBrief"] == inputs["creative_brief"]
    assert PROFILE == "allwe-kling-v34-natural-first-v1"


@pytest.mark.parametrize("duration", [4, 16])
def test_duration_outside_release(duration):
    with pytest.raises(ValueError):
        max_shots_for_duration(duration)


def test_simple_natural_action_policy_does_not_make_walking_default():
    assert "WRITE THE VIDEO_PROMPT FIRST" in ACTION_SYSTEM_PROMPT
    assert "not a checklist of tiny movements" in ACTION_SYSTEM_PROMPT
    assert "same real person" in ACTION_SYSTEM_PROMPT
    assert "natural full-speed" in DIRECTIVE
    assert "CUT TO" in DIRECTIVE
    assert "HARD CUT TO" not in DIRECTIVE
    assert PROFILE == "allwe-kling-v34-natural-first-v1"


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (5, True),
        (6, True),
        (8, True),
        (10, True),
        (15, True),
        (4, False),
        (16, False),
    ],
)
def test_real_route_eligible_only_for_exact_classic_omni(duration, expected):
    base = dict(
        enabled=True,
        generation_mode="automatic",
        duration=duration,
        cut_mode="explicit_cuts",
        provider="cli",
        model="kling-video-v3_0_omni",
        ai_model="gpt-6.1-sol",
    )
    assert native_v34_eligible(**base) is expected
    if duration == 10:
        assert native_i_eligible(**base) is True
    for key, wrong in [
        ("enabled", False),
        ("generation_mode", "custom"),
        ("cut_mode", "continuous_when_possible"),
        ("provider", "api"),
        ("model", "kling-video-v4_0"),
        ("ai_model", "gpt-6.1-terra"),
    ]:
        assert not native_v34_eligible(**{**base, key: wrong})


def sample(count):
    origin = fixture_solo_plan().variants[0]
    beats = [
        origin.beats[0].model_copy(update={"transition": "start" if i == 0 else "cut"})
        for i in range(count)
    ]
    sections = [
        "The original wearer immediately raises one eyebrow and lightly touches "
        "the side of the sweater while the phone moves closer.",
        "CUT TO a close-up of the existing graphic, with hands clear of lettering.",
        "CUT TO a medium view as the person reacts with a knowing smile.",
        "CUT TO a wider contextual view with an easy natural shift of shoulders.",
        "CUT TO a new front product view with relaxed arms.",
        "CUT TO a supported final angle with the original print still visible.",
    ]
    return origin.model_copy(
        update={
            "beats": beats,
            "native_video_prompt": " ".join(sections[:count]),
        }
    )


@pytest.mark.parametrize(
    ("duration", "count"),
    [
        (5, 2),
        (5, 3),
        (8, 3),
        (10, 3),
        (10, 4),
        (10, 5),
        (15, 4),
        (15, 6),
    ],
)
def test_auto_compiler_uses_native_natural_language_not_old_timeline(duration, count):
    result = compile_creative_variants(
        observations=fixture_solo_facts(),
        batch=PlannedVariantBatch(variants=[sample(count)]),
        storyboard_count=1,
        product_count=1,
        variant_count=1,
        duration_seconds=duration,
        reference_role_prompt="References: @image_1 wearer; @image_2 garment",
        cut_mode="explicit_cuts",
        experimental_v25=True,
        native_v34=True,
        local_content_review=False,
    )[0]
    assert result.prompt.count("CUT TO") == count - 1
    assert "Ordered shots:" not in result.prompt
    assert "Editing rhythm" not in result.prompt
    assert "lightly touches the side" in result.prompt
    assert "exact garment design" in result.prompt
    assert result.beats[-1]["endSeconds"] == duration
    assert len(result.prompt) < 3072


def test_wire_and_existing_i_baseline_survive_production_change():
    animal = expand_wire_response(_wire("a1"), native_v34=True).variants[0].beats[0]
    assert animal.interaction_partner_ids == []
    assert animal.used_prop_ids == ["a1"]
    original = old_i_compile(_native_variant())
    assert original.prompt.count("HARD CUT TO") == 4
    assert "10s vertical 9:16 handheld" in original.prompt


def test_no_local_creative_review_reintroduced_for_production():
    # Respect Classic 5c542a8: V3.4 must not introduce new local content scoring.
    simple = "The wearer looks to camera and moves her hand beside the shirt. CUT TO the artwork."
    prompt = compile_native_v34_prompt(
        simple,
        storyboard_count=1,
        product_count=1,
        duration=10,
        shot_count=4,
        cuff_finish="unknown",
        hem_finish="unknown",
        enforce_content_review=False,
    )
    assert "CUT TO the artwork" in prompt
    assert len(list(CUT_MARKER.finditer(prompt))) == 1


def test_lan_boot_enables_feature_and_keeps_i_as_rollback(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import classic_lan

    secret = tmp_path / "secret.txt"
    secret.write_text("unit-test-only", encoding="utf8")
    monkeypatch.setattr(classic_lan, "SECRET_FILE", secret)
    monkeypatch.setattr(classic_lan, "generation_authorized", lambda: True)
    monkeypatch.setattr(classic_lan, "cutflow_environment", lambda _: {})
    env = classic_lan.lan_environment(8002, 5176, "10.12.34.56")
    assert env["AI_OOTD_V1_ENABLED"] == "true"
    assert env["AI_DIRECTOR_V34_ENABLED"] == "false"
    assert env["FASTCUT_UGC_PACING_V1_ENABLED"] == "false"
    assert env["OPENAI_MODEL"] == "gpt-6.2-sol"
    assert env["OPENAI_REASONING_EFFORT"] == "medium"
    assert env["AI_ADAPTER_MODE"] == "openai"
    assert Settings(_env_file=None).ai_director_v34_enabled is False
    assert Settings(_env_file=None).ai_ootd_v1_enabled is False


def test_production_routing_wires_v34_before_i():
    import inspect

    from libtv_agent.services.fixed_video_tasks import create_fixed_video_task

    text = inspect.getsource(create_fixed_video_task)
    assert "native_i = native_i and not native_v34" in text
    assert "native_v34=native_v34" in text
    assert 'marketing_mode="usp" if native_v34' in text
    assert '"multiShot": True if (ootd_enabled or native_i or native_v34)' in text
    assert "creative_variants = await plan_simple_ootd(" in text
    assert "if ootd_enabled:" in text


@pytest.mark.asyncio
async def test_real_cli_argument_compiler_preserves_cut_to_and_image_roles(tmp_path):
    from libtv_agent.adapters.kling_official_cli import KlingOfficialCliAdapter
    from libtv_agent.adapters.video_provider import VideoGenerationRequest
    from test_kling_cli_multishot import _capture_runner, _identity

    story = tmp_path / "story.png"
    product = tmp_path / "product.png"
    story.write_bytes(b"storyboard")
    product.write_bytes(b"product")
    calls = []
    adapter = KlingOfficialCliAdapter(
        Settings(_env_file=None, kling_cli_enabled=True, runner_root=tmp_path / "runner"),
        process_runner=_capture_runner(calls, _identity()),
    )
    p = compile_native_v34_prompt(
        sample(3).native_video_prompt,
        storyboard_count=1,
        product_count=1,
        duration=10,
        shot_count=3,
        cuff_finish="unknown",
        hem_finish="unknown",
    )
    result = await adapter.submit(
        VideoGenerationRequest(
            prompt=p,
            model="kling-video-v3_0_omni",
            image_paths=(story,),
            product_reference_paths=(product,),
            duration=10,
            aspect_ratio="9:16",
            multi_shot=True,
        )
    )
    assert result.provider_task_id == "generated-once"
    args = next(v for v in calls if "omni_ref_video" in v)
    assert args[args.index("--prefer_multi_shots") + 1] == "true"
    assert args[args.index("--enable_audio") + 1] == "false"
    assert args[-1].count("CUT TO") == 2
    assert "图片1" in args[-1] and "图片2" in args[-1]
    assert "@image_1" not in args[-1] and "@image_2" not in args[-1]


def test_compiler_anchors_each_authored_cut_in_time():
    """A cut without a time anchor is a suggestion; Kling then holds one framing."""
    creative = (
        "10s vertical handheld phone UGC, natural full-speed motion. "
        "Start with the wearer standing in the doorway and she lifts the hem "
        "so the print catches the light. "
        "CUT TO a close front view of the complete print and readable lettering. "
        "CUT TO a wider three-quarter view from beside the railing as she turns. "
        "She finishes facing the lens with a small smile."
    )
    base = dict(
        storyboard_count=1, product_count=1, duration=10, shot_count=3,
        cuff_finish="unknown", hem_finish="unknown",
    )
    plain = compile_native_v34_prompt(creative, **base)
    assert "Cuts land at" not in plain
    anchored = compile_native_v34_prompt(creative, shot_windows=[3.0, 3.5, 3.5], **base)
    assert "at 3s CUT TO" in anchored
    assert "at 6.5s CUT TO" in anchored
    assert "Cuts land at 3s, 6.5s" in anchored
    assert "no single framing holds longer than 3.5 seconds" in anchored
    assert anchored.count("CUT TO") == plain.count("CUT TO") == 2
    assert len(anchored) < 3072


def test_shot_window_mismatch_realigns_instead_of_disabling_the_anchor():
    """A drifting beat count must not silently switch the only working lever off.

    Production runs with enforce_content_review=False, so the cut/beat equality
    check in compile_native_v34_prompt never fires; the anchor has to survive the
    mismatch on its own.
    """
    creative = (
        "Start with the wearer standing in the shop doorway while she lifts the "
        "hem so the print catches the daylight. "
        "CUT TO a close front view of the complete print and readable lettering."
    )
    base = dict(
        storyboard_count=1, product_count=1, duration=10, shot_count=4,
        cuff_finish="unknown", hem_finish="unknown", enforce_content_review=False,
    )
    plain = compile_native_v34_prompt(creative, **base)
    assert "Cuts land at" not in plain
    realigned = compile_native_v34_prompt(creative, shot_windows=[2.5, 2.5, 2.5, 2.5], **base)
    assert "at 5s CUT TO" in realigned
    assert "Cuts land at 5s" in realigned
    assert "no single framing holds longer than 5 seconds" in realigned
    assert realigned.count("CUT TO") == plain.count("CUT TO") == 1


def test_shot_window_shortfall_keeps_planned_rhythm_proportion():
    """Fewer planned shots than authored cuts still anchors every authored cut."""
    creative = (
        "Start with the wearer standing in the shop doorway while she lifts the "
        "hem so the print catches the daylight. "
        "CUT TO a close front view of the complete print and readable lettering. "
        "CUT TO a wider three-quarter view from beside the railing as she turns. "
        "CUT TO a supported final angle with the original print still visible."
    )
    base = dict(
        storyboard_count=1, product_count=1, duration=10, shot_count=2,
        cuff_finish="unknown", hem_finish="unknown", enforce_content_review=False,
    )
    anchored = compile_native_v34_prompt(creative, shot_windows=[5.0, 5.0], **base)
    assert "at 2.5s CUT TO" in anchored
    assert "at 5s CUT TO" in anchored
    assert "at 7.5s CUT TO" in anchored
    assert "Cuts land at 2.5s, 5s, 7.5s" in anchored


def test_unanchored_prompt_without_cuts_is_left_alone():
    """Nothing to anchor: never invent a cut that the writer did not author."""
    creative = (
        "The wearer looks down at the print and lifts the hem toward the daylight, "
        "then settles into a relaxed three-quarter stance by the railing."
    )
    base = dict(
        storyboard_count=1, product_count=1, duration=10, shot_count=2,
        cuff_finish="unknown", hem_finish="unknown", enforce_content_review=False,
    )
    plain = compile_native_v34_prompt(creative, **base)
    assert compile_native_v34_prompt(creative, shot_windows=[5.0, 5.0], **base) == plain
    assert compile_native_v34_prompt(creative, shot_windows=[0.0, 10.0], **base) == plain
