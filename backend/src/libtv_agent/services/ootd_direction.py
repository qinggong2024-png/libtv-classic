"""Small creative inputs for OOTD, never a validation or submission gate."""

from __future__ import annotations

from typing import Literal

DisplayFocus = Literal["overall", "print", "material", "fit", "back"]
REVISION = "ootd-natural-baseline-restored-v1"
DETAIL_FOCUSES = frozenset({"print", "material", "back"})
FOCUS_LABELS = {
    "overall": "整体穿搭",
    "print": "印花与文字",
    "material": "材质与细节",
    "fit": "版型与垂坠",
    "back": "背面设计",
}
FOCUS_GUIDANCE = {
    "overall": "Prioritize outfit silhouette, proportions and drape through natural display "
    "actions and orientation changes supported by the references; not continuous frontal display.",
    "print": "Prioritize a complete, unobstructed readable view of the original artwork and "
    "lettering during a deliberate display moment, not throughout the clip. Keep the entire "
    "design in that shot; reveal it through framing or body orientation, not fabric tension.",
    "material": "Show only visible texture and construction through framing and cloth response. "
    "Do not infer fiber composition or performance.",
    "fit": "Show visible shoulder line, garment length, volume or drape through natural movement. "
    "Do not invent fit claims, measurements or unseen construction.",
    "back": "Reveal the back design only with visible reference evidence. Without back evidence, "
    "show a supported garment detail instead; do not invent a reverse-side design or force a turn.",
}
VARIANT_DIRECTIONS = (
    (
        "focus",
        "重点展示",
        "Lead with the selected display focus using the source pose and available evidence.",
    ),
    (
        "detail",
        "细节呈现",
        "Use different framing to reveal a supported product detail related to the selected focus.",
    ),
    (
        "context",
        "场景行为",
        "Show the selected focus through connected everyday behavior motivated by the scene.",
    ),
    (
        "silhouette",
        "穿搭轮廓",
        "Show the selected focus in the whole outfit, with a distinct body orientation or framing.",
    ),
)
INTENSITY_GUIDANCE = {
    "low": "Small, complete body movements at everyday speed; do not slow down or freeze.",
    "standard": "Clear, complete physical actions that reveal the selected garment features, "
    "with moderate movement amplitude at everyday speed.",
    "high": "Expressive, credible body movement at everyday speed, without yanks or forced stunts.",
}


def use_product_evidence(focus: str, explicitly_requested: bool, product_count: int) -> bool:
    return product_count > 0 and (explicitly_requested or focus in DETAIL_FOCUSES)


def creative_controls(
    *,
    focus: str,
    analyze_products: bool,
    brand_tone: str,
    action_intensity: str,
    variant_count: int,
) -> dict[str, object]:
    """Snapshot the inputs actually sent, separate from unverified output quality."""
    return {
        "revision": REVISION,
        "displayFocus": focus,
        "displayFocusLabel": FOCUS_LABELS.get(focus, FOCUS_LABELS["overall"]),
        "productImagesAnalyzed": analyze_products,
        "brandTone": " ".join(brand_tone.split())[:240],
        "brandToneApplied": False,
        "actionIntensity": action_intensity,
        "variantDirections": [
            {"id": f"natural-{index}", "label": f"自然编排 {index}"}
            for index in range(1, variant_count + 1)
        ],
    }
