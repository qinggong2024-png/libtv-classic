"""No-network regression for multi-reference V2.5 Kling prompt packing."""
from libtv_agent.services.ai_creative_planner import (
    V25_PROMPT_MAX_CHARS,
    _compact_provider_prompt,
)
from libtv_agent.services.fixed_video_tasks import _reference_role_prompt
from test_ai_creative_planner import fixture_solo_plan


def test_four_storyboards_three_product_views_preserve_all_shot_boundaries():
    source = fixture_solo_plan().variants[0]
    entries = (
        "The open carton rests on her lap, with the black sweater visible in tissue.",
        "She lifts the same black sweater from the carton at chest height.",
        "She now wears that sweater in the same room, facing the mirror.",
        "She is wearing the same garment with its front facing the phone.",
    )
    exits = (
        "She tips the carton forward while her hands support the same sweater.",
        "She has raised the sweater and turned its complete front to camera.",
        "She turns a little sideways so the hanging hem is visible.",
        "She finishes her step, with clear sleeves and full front artwork.",
    )
    actions = (
        "She lifts the box toward the lens and gives a brief amused look.",
        "She lifts the sweater out and lets its sleeves and hem hang freely.",
        "She lowers her hand from the shoulder and turns slightly sideways.",
        "She walks one step forward and smiles when the sweater settles.",
    )
    cameras = (
        "Handheld camera quickly reframes from her face to the box.",
        "Cut medium shot, then follow the garment from collar to hem.",
        "Cut to her mirror view and widen while she turns.",
        "Cut to a medium-wide shot and drift back a little with her step.",
    )
    proofs = (
        "Keep the precise print, punctuation and red borders from product image.",
        "Keep the same collar, full front design and knitted hem.",
        "Keep garment drape and the untucked hem above black trousers.",
        "Show complete front artwork while both hands stay clear.",
    )
    segments = []
    for i, (start, end) in enumerate(((0, 3), (3, 7), (7, 11), (11, 15))):
        beat = source.beats[0].model_copy(update={
            "frame_order": i + 1,
            "shot_role": ("hook", "reveal", "proof", "closing")[i],
            "transition": "start" if i == 0 else "cut",
            "entry_state": entries[i],
            "action": actions[i],
            "exit_state": exits[i],
            "camera": cameras[i],
            "product_proof": proofs[i],
        })
        segments.append((start, end, beat))
    result = _compact_provider_prompt(
        reference_role_prompt=_reference_role_prompt(
            storyboard_count=4, subject_image_count=3, element_count=0,
        ),
        identity_legend=(
            "Frame-local actors: @image_1: f1_p1=woman_A; "
            "@image_2: f2_p1=woman_A; @image_3: f3_p1=woman_A; "
            "@image_4: f4_p1=woman_A. "
        ),
        duration_seconds=15,
        brand_tone="Alternative Christmas humor; real handheld American UGC",
        products="Same black patterned sweater",
        garment_material="unknown",
        edge_negative="",
        variant=source,
        segments=segments,
        max_prompt_chars=V25_PROMPT_MAX_CHARS,
        preserve_complete_fields=True,
    )
    assert len(result) <= V25_PROMPT_MAX_CHARS
    assert result.count("Start:") == 4
    assert result.count("End:") == 4
    assert result.count("Action:") == 4
    assert result.count("Camera:") == 4
    assert result.count("Product:") == 4
    for value in (*entries, *exits, *actions, *cameras, *proofs):
        assert value in result
    for index in range(1, 8):
        assert f"@image_{index}" in result
    assert "Product refs fix garment art and text" in result
