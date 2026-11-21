"""I-variant native Kling 3.0 Omni five-shot director contract.

This is generation-side direction, not FFmpeg editing. It is only selected
for newly created 10s automatic explicit-cut Classic CLI tasks.
"""
from __future__ import annotations
import re
from libtv_agent.templates.garment_materials import GarmentEdgeFinish

PROFILE = "allwe-kling-i-native-five-shot-v1"
DIRECTIVE = (
    "KLING 3.0 OMNI SMART-SHOTS CROSS-PRODUCT TEST. For a TEN-second realistic vertical "
    "smartphone UGC video, describe exactly FIVE VISUALLY DISTINCT camera views "
    "and precisely FOUR literal HARD CUT TO transitions, as a single fluent "
    "English video direction. Keep the actual model-authored video_prompt "
    "compact (roughly 950-1250 characters), complete and not cut off mid-sentence; "
    "other schema fields carry buyer reasons. Camera cuts are instantaneous, "
    "human gestures are ordinary real-world speed. Views: (1) an immediate product-linked "
    "HOOK with a visibly alive, camera-aware person from the genuine first frame, "
    "not slowly looking down or posing; (2) TRUE FRONT product art/text CLOSE-UP "
    "held legibly and with ALL HANDS CLEAR of wording and central artwork; "
    "(3) noticeably wider natural human/garment proof with a brief spontaneous "
    "reaction; (4) a different reference-supported product view, REAR ONLY IF "
    "explicitly shown in an actual storyboard, otherwise another grounded side "
    "or frontal angle with new useful information; (5) HARD CUT BACK to a clean "
    "FRONT-FACING shot with human expression and readable front graphic. "
    "Aim roughly 1.5-2.5s per shot with the last shot calm and readable, "
    "no sub-second late flash cuts, no empty slow turn or stagnant hold. "
    "Keep clothing exactly per product references, identity, real location and "
    "background per storyboard; no new design, logos, text, props or companions. "
    "No artificial smooth-motion, frozen faces, awkward hand gestures, voice, "
    "music, captions or made-up offers. Do not create extra locations. "
    "If the references constrain framing, avoid inventing unsupported back art."
)

def native_i_eligible(*, enabled: bool, generation_mode: str, duration: int,
                      cut_mode: str, provider: str, model: str, ai_model: str) -> bool:
    return bool(enabled and generation_mode=="automatic" and duration==10
                and cut_mode=="explicit_cuts" and provider=="cli"
                and model=="kling-video-v3_0_omni"
                and ai_model.strip().casefold() in {"gpt-6.1-sol", "gpt-6.2-sol"})


def compile_native_i_prompt(text: str, *, storyboard_count: int,
                            product_count: int, duration: int,
                            cuff_finish: GarmentEdgeFinish,
                            hem_finish: GarmentEdgeFinish,
                            enforce_content_review: bool = True) -> str:
    """Wrap Sol's original video direction in the existing reference contract."""
    # Local import keeps the ordinary planner free of a module import cycle.
    from libtv_agent.services.ai_creative_planner import CreativePlannerError
    creative=text.strip()
    n = storyboard_count + product_count
    if enforce_content_review:
        if len(creative)<80 or len(creative)>1600:
            raise CreativePlannerError("I native video prompt length invalid")
        if creative.lower().count("hard cut to") != 4:
            raise CreativePlannerError("I native video prompt requires four explicit hard cuts")
        if not creative.endswith((".","!","?")):
            raise CreativePlannerError("I native video prompt ends incompletely")
        if any(term in creative.casefold() for term in (
            "start:", "end:", "buyer reason:", "frame-local actors:",
            "creative idea:", "duration_weight", "json schema",
            "voiceover", "dialogue:", "speaks:", "narration:",
        )):
            raise CreativePlannerError("I native video prompt includes internal metadata or dialogue")
        for match in re.findall(r"@image_(\d+)\b",creative,re.IGNORECASE):
            if not 1<=int(match)<=n:
                raise CreativePlannerError("I native video prompt uses unbound image")
    actors=", ".join(f"@image_{i}" for i in range(1,storyboard_count+1))
    if product_count:
        prod=", ".join(f"@image_{i}" for i in range(storyboard_count+1,n+1))
        role=(
            f"References: {actors} "
            + ("is the source image controlling identity, scene and starting pose; "
               if storyboard_count==1 else
               "are ordered visual references controlling identity, scene and starting pose; ")
            + f"{prod} show only the exact garment. "
              "Preserve its original artwork, lettering, fabric and fit."
        )
    else:
        role=(f"References: {actors} are ordered visual references controlling identity, "
              "scene, starting pose and exact garment design.")
    edges=[]
    if cuff_finish=="non_ribbed":edges.append("cuffs")
    if hem_finish=="non_ribbed":edges.append("hem")
    if edges:role+=f" Keep the {' and '.join(edges)} non-ribbed as in the product."
    final=f"{role}\n{duration}s vertical 9:16 handheld smartphone video. {creative}"
    if len(final)>3072:
        raise CreativePlannerError("I native prompt exceeds Kling limit")
    return final


def known_animal_ids(frame) -> set[str]:
    """Only photograph-grounded real animal props may be reclassified as props."""
    if frame is None:return set()
    animal=re.compile(
        r"\b(?:capybaras?|dogs?|cats?|horses?|rabbits?|deer|birds?|"
        r"ducks?|geese|gecko|lizards?|squirrels?|otters?|animals?)\b|"
        r"水豚|猫咪?|狗|兔子?|鹿|鸟|鸭|鹅|松鼠|动物",re.I)
    excluded=re.compile(
        r"\b(?:print(?:ed)?|artwork|graphic|pattern|illustration|"
        r"picture|photo(?:graph)?|plush|toy|statue|drawing)\b|"
        r"印花|图案|插画|玩具|雕像",re.I)
    return {prop.id for prop in frame.props
            if animal.search(prop.evidence) and not excluded.search(prop.evidence)}
