from __future__ import annotations

import re
from typing import Literal

# The two legacy IDs remain readable by older tasks, fixtures and vision responses.
GarmentMaterial = Literal[
    "short_mohair",
    "short_mohair_cardigan",
    "mesh_jersey",
    "washed_tee",
    "cotton_tee",
    "sweatshirt",
    "cotton",
    "unknown",
]

_MOHAIR_KEYWORDS = ("短马海毛", "马海毛", "mohair")
_SWEATER_KEYWORDS = ("毛衣", "针织衫", "sweater", "jumper")
_CARDIGAN_KEYWORDS = ("开衫", "cardigan")
_MESH_JERSEY_KEYWORDS = ("网眼球衣", "网眼", "球衣", "mesh jersey", "mesh", "jersey")
_WASHED_KEYWORDS = ("水洗", "洗水", "washed", "garment-dyed", "garment dyed")
_COTTON_KEYWORDS = ("棉质", "纯棉", "全棉", "棉t", "棉 t", "棉t恤", "cotton")
_TEE_KEYWORDS = ("t恤", "t-shirt", "t shirt", "tee", "短袖")
_SWEATSHIRT_KEYWORDS = ("卫衣", "sweatshirt", "hoodie", "hooded sweatshirt")

# Guidelines describe visible fabric behavior. Exact artwork and garment
# construction (including whether edges are ribbed) ALWAYS follow product refs.
SHORT_MOHAIR_MATERIAL_GUIDANCE = (
    "衣服是短马海毛材质。表现细密短绒、轻微柔软毛感、自然织物厚度与垂坠；"
    "领口、袖口、下摆和有无罗纹严格以商品参考图为准，不得自动增删罗纹或加长绒毛。"
)
SHORT_MOHAIR_CARDIGAN_MATERIAL_GUIDANCE = (
    "短马海毛开衫毛衣：细密短绒、柔和毛感、自然针织垂坠。"
    "开襟形状、门襟、扣子（仅当原图存在）、领口、袖口和下摆严格按商品图还原；"
    "不擅自封闭开襟、添加扣子或改变有无罗纹。"
)
MESH_JERSEY_MATERIAL_GUIDANCE = (
    "参考商品图，保持印花、号码、文字和结构一致；网眼、轻薄感与垂坠自然。"
    "细密小孔随布料受力合理变化，孔眼不得漂移、闪烁、变成大洞或变成平滑棉布。"
)
WASHED_TEE_MATERIAL_GUIDANCE = (
    "水洗短袖：柔软哑光针织布面，水洗后的自然洗旧层次和轻微褪色只按商品图表现；"
    "自然细褶和垂坠，不额外添加破洞、污渍、夸张做旧或改动原有印花文字。"
)
COTTON_TEE_MATERIAL_GUIDANCE = (
    "纯棉短袖：自然哑光棉针织表面、轻微布纹、真实细褶与柔和垂坠；"
    "克重、厚薄、领口、袖口、衣长、颜色及印花均以商品图为准，不要塑料光泽。"
)
SWEATSHIRT_MATERIAL_GUIDANCE = (
    "普通卫衣：真实卫衣针织布面，适度厚度、柔软挺度、自然折痕和穿着垂坠；"
    "是否有内里绒毛、罗纹领口袖口下摆、帽子或拉链均以商品图为准，"
    "不得无依据添加马海毛长绒、网眼或额外结构。"
)
# Retain the old generic material for non-tee explicitly cotton items.
COTTON_MATERIAL_GUIDANCE = (
    "参考商品图，保持印花、款式、文字和结构一致；棉布纹理、褶皱与垂坠自然。"
)
UNKNOWN_MATERIAL_GUIDANCE = "参考商品图，保持印花、款式、文字、材质和结构一致。"

_MATERIAL_GUIDANCE: dict[GarmentMaterial, str] = {
    "short_mohair": SHORT_MOHAIR_MATERIAL_GUIDANCE,
    "short_mohair_cardigan": SHORT_MOHAIR_CARDIGAN_MATERIAL_GUIDANCE,
    "mesh_jersey": MESH_JERSEY_MATERIAL_GUIDANCE,
    "washed_tee": WASHED_TEE_MATERIAL_GUIDANCE,
    "cotton_tee": COTTON_TEE_MATERIAL_GUIDANCE,
    "sweatshirt": SWEATSHIRT_MATERIAL_GUIDANCE,
    "cotton": COTTON_MATERIAL_GUIDANCE,
    "unknown": UNKNOWN_MATERIAL_GUIDANCE,
}

# Brief English phrases for video models; never replace garment image matching.
_VIDEO_MATERIAL_HINT: dict[GarmentMaterial, str] = {
    "short_mohair": (
        "fine dense SHORT brushed mohair-style fibers, subtle soft fuzz and natural knit drape; "
        "edge finishes only as supported by the product image"
    ),
    "short_mohair_cardigan": (
        "fine short brushed mohair-style knit, soft natural drape; preserve the actual open "
        "front, placket, buttons only if visible and all edge construction from the product image"
    ),
    "mesh_jersey": (
        "fine consistent tiny open mesh holes, lightweight athletic jersey drape; "
        "never smooth solid cotton or enlarged/flickering holes"
    ),
    "washed_tee": (
        "soft matte washed T-shirt knit, subtle irregular lived-in fading only as pictured; "
        "no invented damage, dirt or excessive distressing"
    ),
    "cotton_tee": (
        "matte fine cotton jersey T-shirt texture, natural soft creases and drape; "
        "original weight and neckline from product image"
    ),
    "sweatshirt": (
        "ordinary soft sweatshirt knit, believable weight and folds; "
        "never invent fleece lining, mohair fuzz, ribbing or hoodie details"
    ),
    "cotton": "matte natural cotton fabric texture and wrinkles as shown",
    "unknown": "",
}

# User-maintained still-image fusion preset: all garment-edge structure is ref-driven.
SHORT_MOHAIR_IMAGE_PROMPT = (
    "图1模特穿上图2的短马海毛毛衣。材质呈现细密短绒、自然轻柔毛感，不夸张成长毛绒。"
    "领口、袖口和下摆的面料及是否有罗纹完全以图2商品图为准，"
    "不增加、删除或改造罗纹。自然穿着褶皱，光线统一，图2衣服图案版型100%还原。"
)


def infer_garment_material(*values: str | None) -> GarmentMaterial:
    """Classify only explicit product wording; mixed garment types stay unknown.

    A product-only image remains the final design authority. Generic "sweater"
    and "cardigan" names do not establish a short-mohair composition; require
    explicit mohair wording or confident multimodal product evidence.
    """
    text = " ".join(value for value in values if value).casefold()
    has_sweater = any(
        key in text for key in (*_SWEATER_KEYWORDS, *_MOHAIR_KEYWORDS, *_CARDIGAN_KEYWORDS)
    )
    has_cardigan = any(key in text for key in _CARDIGAN_KEYWORDS)
    has_jersey = any(key in text for key in _MESH_JERSEY_KEYWORDS)
    has_sweatshirt = any(key in text for key in _SWEATSHIRT_KEYWORDS)
    has_tee = any(key in text for key in _TEE_KEYWORDS)
    has_washed = any(key in text for key in _WASHED_KEYWORDS)
    has_cotton = any(key in text for key in _COTTON_KEYWORDS)
    # A T-shirt without fabric information is not automatically cotton.
    has_tee_material = has_tee and (has_washed or has_cotton)
    groups = [has_sweater, has_jersey, has_sweatshirt, has_tee_material]
    if sum(groups) > 1:
        return "unknown"
    if has_sweater:
        if has_tee or not any(key in text for key in _MOHAIR_KEYWORDS):
            return "unknown"
        return "short_mohair_cardigan" if has_cardigan else "short_mohair"
    if has_jersey:
        return "mesh_jersey"
    if has_sweatshirt:
        return "sweatshirt"
    if has_tee_material:
        return "washed_tee" if has_washed else "cotton_tee"
    if has_cotton and not has_tee:
        return "cotton"
    return "unknown"


GarmentEdgeFinish = Literal["ribbed", "non_ribbed", "unknown"]


def confirmed_edge_finishes(creative_brief: str) -> tuple[GarmentEdgeFinish, GarmentEdgeFinish]:
    """Read explicit user correction only; do not infer edge construction from a category.

    Return (cuff, hem). An empty/ambiguous brief leaves the photo/model as authority.
    """
    negative_pattern = (
        r"无罗纹|没有罗纹|非罗纹|不是罗纹|无螺纹|没有螺纹|非螺纹|不是螺纹|"
        r"no ribbed|non.ribbed|not ribbed|without ribbing|"
        r"不要(?:添加)?罗纹|禁止(?:添加)?罗纹|不要(?:添加)?螺纹"
    )
    cuff_negative = False
    hem_negative = False
    for clause in re.split(r"[，,；;。.!]+", creative_brief.casefold()):
        if not re.search(negative_pattern, clause):
            continue
        mentions_cuff = bool(re.search(r"袖口|袖边|cuff|sleeve edge", clause))
        mentions_hem = bool(re.search(r"下摆|底摆|hem|bottom edge", clause))
        if not mentions_cuff and not mentions_hem:
            # Unqualified explicit "no ribbing" covers both edges.
            cuff_negative = hem_negative = True
        cuff_negative |= mentions_cuff
        hem_negative |= mentions_hem
    return (
        "non_ribbed" if cuff_negative else "unknown",
        "non_ribbed" if hem_negative else "unknown",
    )


def garment_edge_negative_guidance(
    cuff: GarmentEdgeFinish, hem: GarmentEdgeFinish,
) -> str:
    """Negative prompts only for confirmed absence, never for unknown edges."""
    constraints = []
    if cuff == "non_ribbed":
        constraints.append("NO ribbed cuffs; do not add rib-knit sleeve ends")
    if hem == "non_ribbed":
        constraints.append("NO ribbed hem; do not add a rib-knit waistband")
    if not constraints:
        return ""
    return (
        "Negative garment structure: " + "; ".join(constraints)
        + "; preserve reference edge finishing."
    )


def garment_material_guidance(material: GarmentMaterial) -> str:
    return _MATERIAL_GUIDANCE[material]


def garment_video_material_hint(material: GarmentMaterial) -> str:
    return _VIDEO_MATERIAL_HINT[material]
