"""Admin-only, AI-billed V2.5 planning preview; never submits video work."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.domain.models import Asset, ParentTask, StagedUpload
from libtv_agent.domain.schemas import VideoTaskCreate
from libtv_agent.services.ai_creative_planner import (
    CreativePlannerError,
    plan_creative_video,
)
from libtv_agent.services.analysis import _analysis_image_url
from libtv_agent.services.brand_identity import append_brand_reference
from libtv_agent.services.content_catalog import get_merged_site_style_profile
from libtv_agent.services.fixed_video_tasks import (
    MAX_KLING_PROMPT_CHARS,
    _reference_role_prompt,
)
from libtv_agent.services.page_access import management_admin
from libtv_agent.templates.garment_materials import infer_garment_material
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

router = APIRouter(prefix="/admin", tags=["admin"])
_preview_lock = asyncio.Lock()


class CreativeV25PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taskId: uuid.UUID
    model: Literal["gpt-6.1-sol", "gpt-6.2-sol", "gpt-6.1-terra"] = "gpt-6.2-sol"
    variantCount: Literal[1, 2] = 1
    creativeMode: Literal["showcase", "usp"] = "showcase"
    confirmModelCost: Literal[True]


async def _existing_reference_inputs(session: Session, task: ParentTask):
    """Read only existing files owned by a saved task, in original role order."""
    assets = list(
        await session.scalars(
            select(Asset)
            .where(
                Asset.parent_task_id == task.id,
                Asset.deleted_at.is_(None),
                Asset.role.in_(("STORYBOARD", "PRODUCT")),
            )
            .order_by(Asset.order_no)
        )
    )
    if assets:
        stories = [a for a in assets if a.role == "STORYBOARD"]
        products = [a for a in assets if a.role == "PRODUCT"]
        return stories, products, None

    raw = (task.settings_json or {}).get("_pendingAutomaticVideoSubmission")
    if not isinstance(raw, dict):
        raise HTTPException(
            422, detail={"code": "PREVIEW_ASSETS_MISSING", "message": "此历史任务没有可用的参考图"}
        )
    try:
        saved = VideoTaskCreate.model_validate(raw)
    except ValueError:
        raise HTTPException(
            422, detail={"code": "PREVIEW_INPUT_INVALID", "message": "历史任务的分镜参数无效"}
        ) from None
    if saved.element_ids:
        raise HTTPException(
            422,
            detail={
                "code": "PREVIEW_ELEMENTS_UNSUPPORTED",
                "message": "此预览暂不支持依赖历史主体元素的任务",
            },
        )
    ids = [i.upload_id for i in (*saved.storyboards, *saved.subject_images)]
    rows = list(
        await session.scalars(
            select(StagedUpload).where(
                StagedUpload.id.in_(ids),
                StagedUpload.claimed_task_id == task.id,
                StagedUpload.user_id == task.user_id,
            )
        )
    )
    found = {r.id: r for r in rows}
    if len(found) != len(ids):
        raise HTTPException(
            422, detail={"code": "PREVIEW_ASSETS_MISSING", "message": "部分任务图片已不可用"}
        )
    return (
        [found[i.upload_id] for i in sorted(saved.storyboards, key=lambda x: x.order)],
        [found[i.upload_id] for i in sorted(saved.subject_images, key=lambda x: x.order)],
        saved,
    )


@router.post("/creative-v25-preview")
async def preview_creative_v25(
    body: CreativeV25PreviewRequest,
    actor: Actor,
    session: Session,
    request: Request,
):
    """Synchronous admin trial; absolutely no task claims, queue or Kling calls."""
    if not management_admin(actor):
        raise HTTPException(403, detail={"code": "ADMIN_REQUIRED", "message": "仅平台管理员可预览"})
    if request.app.state.settings.ai_ootd_v1_enabled:
        raise HTTPException(
            410,
            detail={
                "code": "USP_PREVIEW_DISABLED",
                "message": "USP预览已暂时关闭，新的自动任务使用Sol OOTD导演。",
            },
        )
    if _preview_lock.locked():
        raise HTTPException(
            429, detail={"code": "PREVIEW_BUSY", "message": "已有一项 AI 导演预览正在运行"}
        )
    async with _preview_lock:
        task = await session.get(ParentTask, body.taskId)
        if task is None or task.workflow_kind != "FIXED_TEMPLATE":
            raise HTTPException(
                404,
                detail={
                    "code": "PREVIEW_TASK_NOT_FOUND",
                    "message": "没有找到可预览的固定模板任务",
                },
            )
        stories, products, saved = await _existing_reference_inputs(session, task)
        if not (1 <= len(stories) <= 7 and 1 <= len(products) <= 4):
            raise HTTPException(
                422,
                detail={
                    "code": "PREVIEW_IMAGE_COUNT",
                    "message": "需要 1–7 张分镜图和 1–4 张商品图",
                },
            )
        if len(stories) + len(products) > 7:
            raise HTTPException(
                422,
                detail={
                    "code": "PREVIEW_REFERENCE_LIMIT",
                    "message": "该任务超过可灵最多 7 张参考图的生产限制",
                },
            )
        source = task.settings_json or {}
        site = source.get("cutflowSite")
        site_reference = site.get("brandReference") if isinstance(site, dict) else None
        profile_id = task.site_profile_id or (saved.site_profile_id if saved else None)
        if not profile_id:
            raise HTTPException(
                422, detail={"code": "PREVIEW_SITE_UNKNOWN", "message": "任务没有绑定商品站点"}
            )
        try:
            profile = await get_merged_site_style_profile(session, profile_id)
        except ValueError:
            raise HTTPException(
                422, detail={"code": "PREVIEW_SITE_UNKNOWN", "message": "任务的商品站点配置不存在"}
            ) from None
        duration = saved.duration_seconds if saved else source.get("durationSeconds", 10)
        if not isinstance(duration, int) or not 1 <= duration <= 15:
            raise HTTPException(
                422, detail={"code": "PREVIEW_DURATION", "message": "历史任务时长无效"}
            )
        brief = saved.creative_brief if saved else task.user_requirement_raw or ""
        mode = saved.cut_mode if saved else source.get("cutMode", "explicit_cuts")
        intensity = saved.action_intensity if saved else source.get("actionIntensity", "standard")
        reference_prompt = append_brand_reference(
            _reference_role_prompt(
                storyboard_count=len(stories),
                subject_image_count=len(products),
                element_count=0,
            ),
            site_reference,
        )
        object_store = request.app.state.object_store
        settings = request.app.state.settings
        if not settings.openai_api_key or settings.ai_adapter_mode != "openai":
            raise HTTPException(
                503, detail={"code": "PREVIEW_AI_UNAVAILABLE", "message": "AI 导演模型未配置"}
            )

        async def load(items, role):
            return [
                AnalysisImageInput(
                    asset_id=str(item.id),
                    order=index,
                    image_url=await _analysis_image_url(
                        object_store,
                        item.object_key,
                        str(item.id),
                        settings.artifact_root,
                    ),
                    role=role,
                )
                for index, item in enumerate(items, 1)
            ]

        storyboard_images = await load(stories, "STORYBOARD")
        product_images = await load(products, "PRODUCT")
        # Release the SQLite transaction before up to 3 minutes of model latency.
        task_id = str(task.id)
        filenames = [a.original_filename for a in [*stories, *products]]
        task_title = task.title
        await session.rollback()
        pilot = settings.model_copy(
            update={
                "openai_model": body.model,
                "openai_reasoning_effort": (
                    "medium" if body.model == "gpt-6.2-sol"
                    else "low" if body.model == "gpt-6.1-sol" else "none"
                ),
            }
        )
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                plan_creative_video(
                    settings=pilot,
                    storyboard_images=storyboard_images,
                    product_images=product_images,
                    reference_role_prompt=reference_prompt,
                    creative_brief=brief.strip(),
                    brand_tone=profile.tone,
                    target_audience=profile.audience,
                    product_priorities=profile.product_priorities,
                    forbidden_mismatches=profile.forbidden_mismatches,
                    garment_material=infer_garment_material(brief, task_title, *filenames),
                    duration_seconds=duration,
                    variant_count=body.variantCount,
                    cut_mode=mode,
                    action_intensity=intensity,
                    experimental_v25=True,
                    marketing_mode=body.creativeMode,
                ),
                timeout=305,
            )
        except TimeoutError:
            raise HTTPException(
                504,
                detail={
                    "code": "PREVIEW_TIMEOUT",
                    "message": "AI 编排预览超过 305 秒，未自动重试；请核对中转站计费",
                },
            ) from None
        except CreativePlannerError as exc:
            raise HTTPException(
                422
                if exc.code in {"AI_CREATIVE_PLAN_INVALID", "AI_CREATIVE_RESPONSE_INVALID"}
                else 502,
                detail={"code": exc.code, "message": "AI 编排未通过；本轮不会自动重试或提交可灵"},
            ) from None
        return {
            "taskId": task_id,
            "model": body.model,
            "creativeMode": body.creativeMode,
            "variantCount": len(result),
            "storyboardCount": len(stories),
            "productCount": len(products),
            "durationSeconds": duration,
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "klingSubmitted": False,
            "variants": [
                {
                    "idea": variant.creative_idea,
                    "hook": variant.visual_hook,
                    "buyerReason": variant.buyer_reason,
                    "prompt": variant.prompt,
                    "klingPromptWithinLimit": len(variant.prompt) <= MAX_KLING_PROMPT_CHARS,
                    "beats": list(variant.beats),
                    "visualFacts": list(variant.facts),
                    "plannerVersion": variant.planner_version,
                    "requestAttempts": variant.request_attempts,
                }
                for variant in result
            ],
        }
