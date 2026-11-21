"""Default product-aware, cross-scene director guidance for classic OOTD.

This modifies only Sol's creative brief. Kling continues to receive the approved
first frame and unchanged product-reference files; no extra generation tasks.
Set ALLWE_OOTD_CROSS_SCENE_ENABLED=0 and restart classic API to roll back.
"""
from __future__ import annotations

import os

CROSS_SCENE_PROFILE = "allwe-ootd-sol-cross-scene-b-v1"

_SCENE_CONTINUITY_OLD = (
    "Maintain identical original clothing artwork, lettering, colors, texture, "
    "cut, silhouette, wearer, hair, scene, light and original props. "
)
_SCENE_CONTINUITY_NEW = (
    "Maintain identical original clothing artwork, lettering, colors, texture, "
    "cut, silhouette, wearer and hair across ALL shots. The FIRST shot starts "
    "in the exact supplied first-frame setting, pose, camera and lighting. "
    "An intentional DIRECT CUT may establish a new believable everyday "
    "setting; never morph the first room into another location within a shot. "
)
_INVENTION_OLD = (
    "No invented product claims, pricing, promotional promises, props, people, "
    "locations, subtitles, dialogue, soundtrack or overlays. "
)
_INVENTION_NEW = (
    "No invented product claims, pricing, urgency, customer testimony, "
    "extra actors, garment graphics, garment construction, subtitles, "
    "dialogue, soundtrack or overlays. Ordinary plausible scene props and "
    "locations are permitted at a motivated hard cut, but must never "
    "contradict supplied visual references or imply unverified product facts. "
)
_DIRECTOR_GUIDANCE = (
    "PRODUCT-AWARE CROSS-SCENE DIRECTOR: Use original product-reference "
    "images as the only authority for the printed design, exact text, colors, "
    "front/back orientation and shape. Treat the first storyboard frame as "
    "the precise opening only, not as a restriction on every later scene. "
    "Before writing shots, silently infer a plausible buyer motivation "
    "grounded in the visible graphic or identity (self-expression, a real "
    "occasion, bookish conversation or gifting), not generic style praise. "
    "Build a connected everyday micro-event with an observable trigger, "
    "a purposeful human action, an authentic facial response when motivated, "
    "and a visible outcome. The photographer may feel like a friend present "
    "in the moment; do not script a compulsory smile or gaze into camera. "
    "For an 8-15 second clip with a usable product reference, the normal "
    "plan connects the actual first-frame location to ONE distinct, product-"
    "motivated everyday location through a clear DIRECT CUT. Staying in the "
    "first location for the entire clip is a fallback, not the default: use "
    "it only if the user explicitly wants one place or a second occasion "
    "has no believable connection to the garment. Never switch places for "
    "decoration only. A second scene starts "
    "already established on the other side of a DIRECT CUT with the SAME "
    "wearer and original product, never an unsupported continuous walk or "
    "room transformation. Let the director choose 2-5 meaningful shots and "
    "practical durations, and explain human action, emotional cause, "
    "camera movement, environment interaction and visible result naturally. "
    "Keep actor and camera alive at everyday speed without tedious held "
    "expressions or fixed gestures. Leave a natural readable glimpse of "
    "the key garment print; if back artwork is not actually referenced, "
    "never invent its design. When a planned cut is meant to show a "
    "referenced back print, cut directly to the wearer already facing "
    "away at the new shot position. Start close enough to read the "
    "complete print and keep it in frame at a readable size through "
    "the natural action in that setting. Preserve the user's explicit restrictions "
    "on scene, camera, actions or clothing; do not force cross-scenes "
    "against them. UGC is the presentation; product-specific buyer reason "
    "and clear visual proof are the advertising purpose. "
)


def default_cross_scene_brief() -> str:
    return (
        "Create an authentic American UGC mini-story about why this specific "
        "garment belongs in a real everyday occasion, based on visible original "
        "product graphics. Start exactly in the first provided scene. After a "
        "purposeful first beat, DIRECT CUT to a DISTINCT but believable second "
        "place where that buyer motivation matters (a related gathering, "
        "book market, office interaction, holiday event or other natural "
        "occasion chosen from the actual design, not a generic scene list). "
        "Connect each place through meaningful human behavior, natural emotion "
        "and the same wearer. Show the important artwork clearly without "
        "staging static product poses. If the supplied image or user instruction "
        "makes a second place implausible, stay in the first place. "
    )


def cross_scene_enabled() -> bool:
    return os.environ.get("ALLWE_OOTD_CROSS_SCENE_ENABLED", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }


def product_analysis_enabled(*, video_type: str, product_count: int) -> bool:
    """Visual product images go to Sol by default on the classic auto-ootd route."""
    return cross_scene_enabled() and video_type == "ootd" and product_count > 0


def cross_scene_director_prompt(baseline: str) -> str:
    """Use exact in-place conflict removal; unexpected source changes fail closed."""
    if not cross_scene_enabled():
        return baseline
    if baseline.count(_SCENE_CONTINUITY_OLD) != 1 or baseline.count(_INVENTION_OLD) != 1:
        raise ValueError("OOTD baseline prompt changed: audit cross-scene substitution")
    return (baseline.replace(_SCENE_CONTINUITY_OLD, _SCENE_CONTINUITY_NEW)
                    .replace(_INVENTION_OLD, _INVENTION_NEW) + " " + _DIRECTOR_GUIDANCE)
