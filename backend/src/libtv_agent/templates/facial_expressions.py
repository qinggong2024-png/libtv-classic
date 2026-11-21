"""Small, source-compatible expressions; no scenes, personas or acting routines."""

from dataclasses import dataclass
from typing import Literal

MouthState = Literal["neutral", "soft_smile", "open_or_expressive", "unknown"]


@dataclass(frozen=True)
class FaceEvidence:
    mouth: MouthState = "unknown"
    eyes_visible: bool = False

    @property
    def gentle_expression(self) -> bool:
        return self.mouth in {"neutral", "soft_smile"}


UNKNOWN_FACE = FaceEvidence()


@dataclass(frozen=True)
class FacialExpression:
    id: str
    label: str
    instruction: str
    changes_mouth: bool = False


EXPRESSION_LIBRARY = {
    item.label: item
    for item in (
        FacialExpression("relaxed_presence", "放松从容", "沿用原有表情，保持面部放松，不刻意摆拍"),
        FacialExpression(
            "calm_confidence",
            "克制自信",
            "保留原有嘴角和视线，以从容、稳定的神态停留，不压眉或绷脸",
        ),
        FacialExpression(
            "quiet_focus", "自然专注", "沿用原有视线落点，保持轻微专注，不新增转头、挑眉或凝视"
        ),
        FacialExpression(
            "mild_curiosity",
            "轻微好奇",
            "在原有视线附近保持轻微关注感，不挑眉、不睁大眼或转头寻找新对象",
        ),
        FacialExpression(
            "knowing_smile",
            "会意一笑",
            "嘴角轻微扬起一次，形成短暂会意笑意后自然停留，不露齿、不张嘴或做说话口型",
            True,
        ),
        FacialExpression(
            "warm_half_smile",
            "自然浅笑",
            "嘴角轻微放松上扬一次并自然停留，不露齿、不抿嘴或连续切换情绪",
            True,
        ),
        FacialExpression(
            "subtle_playfulness",
            "轻微玩味",
            "嘴角出现一次极轻微笑意并停留，不眨单眼、不挑眉或夸张表演",
            True,
        ),
    )
}

# Interpret existing built-in wording, while never executing arbitrary catalog text.
EXPRESSION_ALIASES = {
    "relaxed neutral expression": "放松从容",
    "calm relaxed expression": "放松从容",
    "neutral confidence": "克制自信",
    "restrained expression": "克制自信",
    "mild curiosity": "轻微好奇",
    "soft thoughtful expression": "自然专注",
    "brief restrained amused smile": "会意一笑",
    "brief amused recognition": "会意一笑",
    "brief amused reaction": "会意一笑",
    "subtle half-smile": "轻微玩味",
    "subtle warm smile": "自然浅笑",
    "subtle pleased smile": "自然浅笑",
}
