"""Five-shot USP packing must prioritize boundaries over repeated header prose."""
from libtv_agent.services.ai_creative_planner import V25_PROMPT_MAX_CHARS, _compact_provider_prompt
from libtv_agent.services.fixed_video_tasks import _reference_role_prompt
from test_ai_creative_planner import fixture_solo_plan


def test_five_shot_usp_with_four_storyboards_three_products_preserves_complete_states():
    source=fixture_solo_plan().variants[0]
    entries=[
        "The Christmas box is already open on her lap and the folded sweater is visible.",
        "She is seated beside the open box and holding the black sweater above the tissue.",
        "She is now wearing the pullover and facing the mirror with her hand at her shoulder.",
        "She stands sideways to the mirror wearing the same sweater and letting sleeves hang.",
        "She faces the handheld phone in the same room, still in the sweater and trousers.",
    ]
    exits=[
        "She tilts the box toward the lens and meets camera with an amused smile.",
        "She unfolds the full knit body and holds the front design up for the viewer.",
        "She turns her torso naturally to show how the sweater fits over dark trousers.",
        "She has pivoted to show the original patterned back, then begins turning to front.",
        "She ends with the complete front print uncovered and both hands lowered.",
    ]
    actions=[
        "She tips the box toward the lens, reads the front, then gives an amused grin.",
        "She lifts it clear of tissue and lets the body unfold under its own weight, smiling down.",
        "She releases her chest touch and turns a quarter sideways to check the sweater.",
        "She continues turning until her back faces the mirror, letting both sleeves hang.",
        "She lowers her reaching hand and takes one easy step forward, letting sweater settle.",
    ]
    cameras=[
        "Handheld pushes from her eyes down to the printed front inside the box.",
        "Medium shot follows the lift and reframes to keep neckline and hem visible.",
        "Cut to mirror angle then drift sideways with the natural turn.",
        "Mirror view pivots in place while retaining room continuity.",
        "Handheld eases backward to capture the full final garment silhouette.",
    ]
    proofs=[
        "The actual reference front appears immediately, with lettering unchanged.",
        "The exact front artwork, printed words, sleeve pattern and pullover cut.",
        "The worn neckline, sleeve length and unforced body drape remain visible.",
        "The patterned text-free back matches the second product reference.",
        "The original front design and red edges are unobstructed at final reveal.",
    ]
    segments=[]
    for i,(begin,end) in enumerate(((0,3),(3,7),(7,10),(10,12),(12,15))):
        beat=source.beats[0].model_copy(update={
            "frame_order":min(i+1,4),
            "shot_role":("hook","reveal","proof","proof","closing")[i],
            "transition":"start" if i==0 else "cut",
            "entry_state":entries[i], "exit_state":exits[i],
            "action":actions[i], "camera":cameras[i], "product_proof":proofs[i],
        })
        segments.append((begin,end,beat))
    prompt=_compact_provider_prompt(
        reference_role_prompt=_reference_role_prompt(storyboard_count=4,subject_image_count=3,element_count=0),
        identity_legend=("Frame-local actors: @image_1: f1_p1=woman_A, f1_p2=unlinked; "
            "@image_2: f2_p1=woman_A; @image_3: f3_p1=woman_A; @image_4: f4_p1=woman_A. "
            "Never equate unlinked actors across frames. "),
        duration_seconds=15,brand_tone="Alternative Christmas humor; authentic phone UGC",
        products="Same black printed sweater",garment_material="unknown",edge_negative="",
        variant=source,segments=segments,
        max_prompt_chars=V25_PROMPT_MAX_CHARS,preserve_complete_fields=True,
    )
    assert len(prompt)<=3072
    for label in ("Start:","End:","Action:","Camera:","Product:"):
        assert prompt.count(label)==5,(label,prompt.count(label),len(prompt))
    for value in (*entries,*exits,*actions,*cameras,*proofs):
        assert value in prompt
    for i in range(1,8):
        assert f"@image_{i}" in prompt
