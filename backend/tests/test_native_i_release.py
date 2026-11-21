"""Classic's tightly gated Sol->Kling 3.0 Omni I-variant release.

No API call, billing or CUT interaction. The approved real capybara response
is additionally replayed against production code by the separate release probe.
"""
from __future__ import annotations

import pytest
from libtv_agent.services.native_five_shot import (
    PROFILE, DIRECTIVE, compile_native_i_prompt, native_i_eligible, known_animal_ids,
)
from libtv_agent.services.ai_creative_planner import (
    compile_creative_variants,PlannedVariantBatch,CreativePlannerError,
)
from libtv_agent.services.creative_plan_v25 import (
    WireFrame,WirePerson,WireProp,WireVariant,WireShot,WireCreativeResponse,
    expand_wire_response,
)
from test_ai_creative_planner import fixture_solo_facts,fixture_solo_plan


@pytest.mark.parametrize(("change","expected"),[
    ({},True),
    ({"enabled":False},False),
    ({"duration":15},False),
    ({"duration":20},False),
    ({"generation_mode":"custom"},False),
    ({"cut_mode":"continuous_when_possible"},False),
    ({"provider":"api"},False),
    ({"model":"kling-4.0"},False),
    ({"ai_model":"gpt-6.1-terra"},False),
])
def test_native_i_is_only_for_authorized_ten_second_auto_cli_sol(change,expected):
    params=dict(enabled=True,generation_mode="automatic",duration=10,
       cut_mode="explicit_cuts",provider="cli",
       model="kling-video-v3_0_omni",ai_model="gpt-6.1-sol")
    params.update(change)
    assert native_i_eligible(**params) is expected


def _native_variant():
    original=fixture_solo_plan().variants[0]
    beats=[original.beats[0].model_copy(update={"transition":"cut"}) for _ in range(5)]
    prompt=(
      "A real everyday person notices the camera with an amused expression. "
      "HARD CUT TO a clear front close-up of the original garment design. "
      "HARD CUT TO a natural wider reaction showing the real room. "
      "HARD CUT TO a distinct garment crop without new details. "
      "HARD CUT TO a readable front-facing human-and-product payoff."
    )
    return original.model_copy(update={
       "native_video_prompt":prompt,"beats":beats,
    })


def _compile(variant,*,native=True,seconds=10):
    return compile_creative_variants(
       observations=fixture_solo_facts(),
       batch=PlannedVariantBatch(variants=[variant]),
       storyboard_count=1,product_count=1,
       variant_count=1,duration_seconds=seconds,
       reference_role_prompt="References: @image_1 person; @image_2 product",
       cut_mode="explicit_cuts",experimental_v25=True,native_five_shot=native
    )[0]


def test_native_compiler_passes_direct_sol_narrative_to_kling():
    variant=_native_variant()
    result=_compile(variant)
    assert PROFILE=="allwe-kling-i-native-five-shot-v1"
    assert "HARD CUT TO" in DIRECTIVE
    assert result.prompt.count("HARD CUT TO")==4
    assert "Ordered shots:" not in result.prompt
    assert "@image_1" in result.prompt and "@image_2" in result.prompt
    assert "10s vertical 9:16 handheld" in result.prompt
    assert len(result.prompt)<=3072


def test_native_contract_rejects_malformed_generation_before_any_paid_submit():
    good=_native_variant()
    with pytest.raises(CreativePlannerError,match="five grounded beats"):
        _compile(good.model_copy(update={"beats":good.beats[:4]}))
    with pytest.raises(CreativePlannerError,match="four explicit hard cuts"):
        _compile(good.model_copy(update={"native_video_prompt":good.native_video_prompt.replace("HARD CUT TO", "The camera drifts toward")}))
    with pytest.raises(CreativePlannerError,match="incompletely"):
        _compile(good.model_copy(update={
            "native_video_prompt":good.native_video_prompt.rstrip("."),
        }))
    with pytest.raises(CreativePlannerError,match="unbound image"):
        _compile(good.model_copy(update={
            "native_video_prompt":good.native_video_prompt+" Look at @image_4.",
        }))


def _frame():
    return WireFrame(
      f=1,counted=True,
      people=[WirePerson(id="p1",evidence="An existing person on camera")],
      props=[WireProp(id="a1",evidence="A real capybara near the fence"),
             WireProp(id="a2",evidence="A capybara illustration printed on a shirt"),
             WireProp(id="hat",evidence="A real baseball cap on a nearby bench")],
      scene="Existing snowy enclosure",pose="Person standing",
      crop="phone medium",light="daylight",garment="original sweater",
      space="existing scene"
    )


def _wire(partner):
    frame=_frame()
    shot=WireShot(
       f=1,role="hook",cut="start",weight=1,why="show print",
       entry="standing",move="person looks toward camera",exit="standing",
       cam="medium",proof="visible original garment",actors=["p1"],
       partners=[partner],props=[],novelty="none",
    )
    return WireCreativeResponse(
       frames=[frame],product="sweater",variants=[
          WireVariant(idea="the wearer expresses a joke",hook="original garment visible",
                      reason="recognizable product humor",video_prompt="A clip.",
                      shots=[shot])
       ],
    )


def test_only_documented_live_animals_retype_as_props():
    corrected=expand_wire_response(_wire("a1"),native_i=True).variants[0].beats[0]
    assert corrected.interaction_partner_ids==[]
    assert corrected.used_prop_ids==["a1"]
    ordinary=expand_wire_response(_wire("a1"),native_i=False).variants[0].beats[0]
    assert ordinary.interaction_partner_ids==["a1"]
    assert ordinary.used_prop_ids==[]
    for never_retype in ("a2","hat","unverified"):
        beat=expand_wire_response(_wire(never_retype),native_i=True).variants[0].beats[0]
        assert beat.interaction_partner_ids==[never_retype]
        assert beat.used_prop_ids==[]
    assert known_animal_ids(_frame())=={"a1"}

@pytest.mark.asyncio
async def test_cli_native_i_passes_true_shot_flag_and_canonical_image_slots(tmp_path):
    from libtv_agent.adapters.kling_official_cli import KlingOfficialCliAdapter
    from libtv_agent.adapters.video_provider import VideoGenerationRequest
    from libtv_agent.config import Settings
    from test_kling_cli_multishot import _identity,_capture_runner
    story=tmp_path/"person.png"
    prod=tmp_path/"sweater.png"
    story.write_bytes(b"storyboard")
    prod.write_bytes(b"product")
    calls=[]
    adapter=KlingOfficialCliAdapter(
        Settings(_env_file=None,kling_cli_enabled=True,runner_root=tmp_path/"runner"),
        process_runner=_capture_runner(calls,_identity()),
    )
    native=compile_native_i_prompt(
        _native_variant().native_video_prompt,
        storyboard_count=1,product_count=1,duration=10,
        cuff_finish="unknown",hem_finish="unknown",
    )
    result=await adapter.submit(VideoGenerationRequest(
        prompt=native,model="kling-video-v3_0_omni",
        image_paths=(story,),product_reference_paths=(prod,),
        duration=10,aspect_ratio="9:16",multi_shot=True,
    ))
    assert result.provider_task_id=="generated-once"
    request=next(v for v in calls if "omni_ref_video" in v)
    assert request[request.index("--prefer_multi_shots")+1]=="true"
    assert request[request.index("--enable_audio")+1]=="false"
    assert "图片1" in request[-1] and "图片2" in request[-1]
    assert "@image_1" not in request[-1] and "@image_2" not in request[-1]
    assert request[-1].count("HARD CUT TO")==4
