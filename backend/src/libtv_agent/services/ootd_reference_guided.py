"""Reference-guided storyboard rules, separate from autonomous cross-scene direction."""

from __future__ import annotations

import re

_OPTIONAL_STORYBOARD_ROLE = (
    "Additional storyboard images are ordered references for optional later "
    "shots, useful framing and natural gestures, never replacement openings "
    "or mandatory frozen poses. "
)
_REQUIRED_STORYBOARD_ROLE = (
    "Additional storyboard images are REQUIRED ordered visual anchors for later "
    "shots, not optional suggestions and never replacement openings. Use every "
    "uploaded storyboard image in its uploaded order. For each such shot, preserve "
    "the actual referenced location, background, viewpoint, outfit orientation, "
    "visible hairstyle, lighting and props; do not move an outdoor reference "
    "into the opening room or keep only its back-facing angle. Natural body "
    "movement may evolve from the pictured pose, but retain the visual scene "
    "and framing intent. Keep the same wearer and accurate garment design across "
    "shots. A change of place between references is a deliberate DIRECT CUT, "
    "not a reason to discard the later reference. Only an explicit user "
    "instruction can override a reference's scene or its inclusion. "
)
_GLOBAL_SCENE_LOCK = (
    "Maintain identical original clothing artwork, lettering, colors, texture, "
    "cut, silhouette, wearer, hair, scene, light and original props. "
)
_SHOT_SCENE_LOCK = (
    "Maintain identical original clothing artwork, lettering, colors, texture, "
    "cut, silhouette and wearer. Preserve the hair, scene, lighting and props "
    "shown in each ordered storyboard shot instead of forcing every shot to "
    "reuse the opening location. "
)
_REFERENCE_LABELS = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七"}
_BAD_ROLE = re.compile(
    r"可选|本片不采用|本片不用|不采用|不使用|不用|忽略|舍弃|跳过|"
    r"不照搬.{0,24}(?:场景|环境|背景|发型|姿势|道具|光线)|"
    r"不沿用.{0,24}(?:场景|环境|背景|发型|姿势|道具|光线)",
)

REFERENCE_GUIDED_REPAIR = (
    "The previous draft downgraded or skipped an uploaded storyboard image. "
    "Rewrite the whole prompt. The first storyboard anchors the opening and "
    "EVERY later storyboard must be represented in order at its actual location, "
    "with its setting, framing, visible props, light and garment orientation. "
    "Only vary plausible motion within the referenced shots. Preserve the "
    "wearer's identity and the product artwork. Keep shots natural and concise. "
    "Return only the corrected Chinese Kling prompt. Invalid prior draft: "
)


def reference_guided_system_prompt(system_prompt: str) -> str:
    """Upgrade only the multi-storyboard reference route, leaving V2 unchanged."""
    if _OPTIONAL_STORYBOARD_ROLE not in system_prompt:
        raise ValueError("Expected optional multi-storyboard role is absent")
    if _GLOBAL_SCENE_LOCK not in system_prompt:
        raise ValueError("Expected generic scene lock is absent")
    return (
        system_prompt.replace(_OPTIONAL_STORYBOARD_ROLE, _REQUIRED_STORYBOARD_ROLE, 1)
        .replace(_GLOBAL_SCENE_LOCK, _SHOT_SCENE_LOCK, 1)
    )


def reference_guided_violation(
    prompt: str, *, storyboard_count: int, creative_brief: str = ""
) -> str | None:
    """Catch explicit decisions to drop or cherry-pick a later visual anchor.

    This is a conservative text-level gate, not an image/scene similarity judge.
    """
    for slot in range(2, storyboard_count + 1):
        cn = _REFERENCE_LABELS[slot]
        label = re.compile(
            rf"@image[_ ]?{slot}\b|\bimage\s*{slot}\b|图\s*{slot}\b|"
            rf"图片?\s*{cn}|第\s*{cn}\s*张",
            re.IGNORECASE,
        )
        explicit_override = any(
            _BAD_ROLE.search(creative_brief[match.start():match.start() + 120])
            for match in label.finditer(creative_brief)
        )
        if explicit_override:
            continue
        for match in label.finditer(prompt):
            # Do not conflate the next image's role or the actual shot narrative.
            fragment = re.split(
                r"[@]image[_ ]?\d+|[。；\n]", prompt[match.end():match.end() + 125],
                maxsplit=1,
            )[0]
            if _BAD_ROLE.search(fragment):
                return f"storyboard_image_{slot}_downgraded"
    return None
