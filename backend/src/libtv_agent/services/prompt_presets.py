import uuid
from typing import Final

from libtv_agent.domain.models import PromptPreset
from libtv_agent.templates.garment_materials import (
    SHORT_MOHAIR_IMAGE_PROMPT,
    garment_material_guidance,
)
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PROMPT_PRESET_NAME: Final = "默认 1"

_DEFAULT_VIDEO_PROMPT: Final = (
    "生成一段动态视频，智能分镜。iPhone17后置拍摄效果模仿手持镜头拍摄，"
    "画面带有轻微自然的晃动。模特做了几个自然的动作展示今天的穿搭，"
    + garment_material_guidance("unknown")
    + "画面整体呈现真实自然，模特皮肤不要过度光滑，保留毛孔细节和绒毛，真实皮肤质感。"
    "\n动作包含：\n"
    "1. 模特做一个自然的展示动作\n"
    "2. 模特展示衣服细节\n"
    "3. 模特再做一个自然的收尾展示动作\n"
    "\n要求画面流畅自然，人物皮肤保留瑕疵细节，动态有松弛感，自然流畅，"
    "保持原图光感和色调风格，画面整体真实自然光线和谐统一，9:16竖屏，高清8K。"
    "真实UGC风格。"
)

DEFAULT_PROMPT_PRESET_SPECS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "image_fusion",
        DEFAULT_PROMPT_PRESET_NAME,
        "以图一为底图，仅将图一人物身上的上衣替换为图二的服装，完整保留图一原始背景、"
        "人物身份、脸部、发型、姿态、下装、构图、机位、光线和色调。不要抠出人物，不要"
        "移除、替换、虚化或重构背景。服装材质严格按图二还原：网眼球衣的小孔清晰稳定；"
        "短马海毛毛衣有细密短毛绒。领口和下摆都是短马海毛面料，不是罗纹。"
        "袖口同为短马海毛面料，不是罗纹。"
        "宽松版型，光线统一自然。衣服自然"
        "贴合身体，有自然褶皱和垂落，融合后衣服的色调光影和环境"
        "统一协调",
    ),
    (
        "image_storyboard",
        DEFAULT_PROMPT_PRESET_NAME,
        "根据图一的人物和环境，生成一张人物别的姿势和拍摄角度的图片，保留人物脸部的毛孔和"
        "绒毛细节，自然的皮肤纹理，人物比例正常，保持图一的色调和光影不变，保持衣服的印花"
        "和结构与已上传的服装参考图片一致。服装材质按商品参考图还原：短马海毛毛衣的"
        "领口和下摆都是短马海毛面料，不是罗纹。袖口同为短马海毛面料，不是罗纹。"
        "宽松版型，光线统一自然；"
        "网眼球衣的小孔"
        "清晰稳定。人物动作自然，表情放松，人物和环境比例"
        "大小正常，人物高度和透视与环境的关系参考图一的身高",
    ),
    (
        "image_fusion",
        "短马海毛",
        SHORT_MOHAIR_IMAGE_PROMPT,
    ),
    (
        "video",
        DEFAULT_PROMPT_PRESET_NAME,
        _DEFAULT_VIDEO_PROMPT,
    ),
)


def add_default_prompt_presets(session: AsyncSession, user_id: uuid.UUID) -> None:
    for scope, name, content in DEFAULT_PROMPT_PRESET_SPECS:
        session.add(
            PromptPreset(
                user_id=user_id,
                scope=scope,
                name=name,
                content=content,
                sort_order=0,
            )
        )
