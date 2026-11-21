"""Validated dynamic OOTD director, isolated from the legacy cross-scene brief.

ALLWE_OOTD_DYNAMIC_ENABLED=0 restores the original cross-scene Sol director
after restarting the classic API. No provider or CUT behavior changes here.
"""
from __future__ import annotations

import os

DYNAMIC_OOTD_PROFILE = "allwe-ootd-sol-dynamic-display-v1"
DYNAMIC_OOTD_SYSTEM = "You are a director of candid American OOTD outfit footage. The video sells the look and recognizability of a real garment on a real wearer, not a plot or a themed sketch. Image 1 is the exact opening frame, including person, pose, existing outfit, physical setting and camera view. The separately labeled original product photo is the sole authority for the garment's front/back graphics, text, color, material, shape and proportions. Preserve it across all cuts.\n\nPrioritize the clothing in motion: the outfit's overall fit and silhouette, the print and readable wording, and the real wearer's effortless everyday presence. The garment design is something to show, not a script for finding matching food, props, locations or other actors. Select familiar plausible filming viewpoints and natural outfit-display behavior. Let movements and expressions happen at human speed: body movement and camera framing should support what the viewer learns about the outfit. Keep the result lively, not a still pose, repeated hand gesture or runway choreography. You may use an appropriate second location or multiple camera angles when they improve the visual presentation, but no storyline or location change is required.\n\nOver 10 seconds, show the outfit through an unfolding simple human activity rather than consecutive frozen display poses. Choose a few shots with genuine, varied motion: the wearer changes position naturally, the clothing moves with them, and the camera responds. Each shot has a visible starting state, a simple physical action, and a new visual result. Give the print a brief naturally settled view to remain legible, then let the other moments feel actively lived-in. Choose shot count and pacing organically rather than using a fixed pattern. Ensure its key print and wording are fully legible at least once; when source images support both front and back, show them clearly rather than a pure side-only view. Keep the same person's face, hairstyle, complete outfit (including lower garments and accessories), and fabric design consistent through cuts. Motion and framing should keep the original printed graphic undistorted and unblocked. Keep props and background incidental rather than constructing a story around the graphic.\n\nOutput only one concise Chinese director prompt describing a 10-second silent 9:16 OOTD. Give consecutive shot intervals with the camera view and a natural visible change in each shot. At every new shot boundary explicitly write 直接切镜（DIRECT CUT） and the next place or angle. No JSON or commentary."
DYNAMIC_OOTD_BRIEF = "Create an authentic American smartphone OOTD clothing showcase, not a mini-story, themed demonstration or narrative about the printed graphic. Start exactly from image 1. Focus on the clothing in natural motion: show its full fit and readable graphics as the wearer casually changes position and the camera catches distinct, useful angles. Let each shot show a modest real physical change with a visible result, without turning the garment's graphic into a literal prop-driven story. A short natural pause for legibility is fine, not an extended stationary hold. The wearer does not need to find an object matching the print or perform an event. A believable second place or angle is optional if it adds visual variety. Keep all worn items consistent, and allow the model to choose natural shot pacing rather than inventing a prop-driven plot."


def dynamic_ootd_enabled() -> bool:
    return os.environ.get("ALLWE_OOTD_DYNAMIC_ENABLED", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }


def dynamic_ootd_brief() -> str:
    return DYNAMIC_OOTD_BRIEF


def dynamic_ootd_prompt(
    *, duration_seconds: int, storyboard_count: int, product_visible_to_sol: bool,
) -> str:
    """Use the exact paid-tested 10s prompt for the single-frame, visible-product route."""
    text = DYNAMIC_OOTD_SYSTEM
    if duration_seconds != 10:
        text = text.replace("Over 10 seconds,", f"Over {duration_seconds} seconds,")
        text = text.replace("a 10-second silent 9:16 OOTD",
                            f"a {duration_seconds}-second silent 9:16 OOTD")
    if storyboard_count > 1:
        text += (
            " Any additional storyboard images are ordered optional later-shot "
            "references, not replacement openings or product photos. Respect "
            "their supported person, outfit and viewing angle without freezing actions."
        )
    if not product_visible_to_sol:
        text = text.replace(
            "The separately labeled original product photo is the sole authority for "
            "the garment's front/back graphics, text, color, material, shape and proportions. "
            "Preserve it across all cuts.",
            "Preserve only garment design details visible in the supplied storyboard images. "
            "Product-reference photos are separately provided to Kling, not shown to you; "
            "do not guess unseen artwork, text, colors, construction or reverse-side graphics."
        )
    return text
