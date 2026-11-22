"""Read-only, evidence-labelled production trace for an administrator's saved clip.

No provider calls, write operations, blind retries, private object-store paths,
tokens, raw provider responses, or billing credentials are exposed.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from libtv_agent.config import get_settings
from libtv_agent.domain.enums import DownloadStatus
from libtv_agent.domain.models import (
    Asset,
    AuditEvent,
    CutflowHandoff,
    DownloadArtifact,
    GenerationAttempt,
    GenerationCommand,
    GenerationSubmissionAttempt,
    GenerationVariant,
    MaterializationOperation,
    MaterializationSaga,
    MultiCutflowTask,
    ParentTask,
    ValidationResult,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(date: datetime | None) -> str | None:
    if date is None:
        return None
    # SQLite's CURRENT_TIMESTAMP and SQLAlchemy timezone columns are stored as UTC
    # but the driver may return naive datetimes.
    return (date if date.tzinfo else date.replace(tzinfo=UTC)).isoformat()


def _enum(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    delta = (end - start).total_seconds()
    return round(delta, 2) if delta >= 0 else None


def _text(value, limit: int = 12000) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit] or None


def _safe_plan_item(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "creativeIdea": _text(value.get("creative_idea"), 1600),
        "visualHook": _text(value.get("visual_hook"), 1000),
        "buyerReason": _text(value.get("buyer_reason"), 1400),
        "masterPrompt": _text(value.get("master_prompt"), 14000),
        "shotInstructions": [
            _text(x, 2200) if isinstance(x, str)
            else {k: _text(v, 1800) for k, v in x.items() if k in {
                "shot", "description", "prompt", "action", "camera", "duration", "time",
            } and isinstance(v, str)}
            for x in (value.get("shot_instructions") or [])[:12]
            if isinstance(x, (str, dict))
        ],
    }


def _confirmed_wire_audit(
    attempt: GenerationAttempt, variant: GenerationVariant, task: ParentTask,
) -> dict | None:
    """Read only this clip's provider-acknowledged immutable wire snapshot.

    Filenames are derived from the stored submission identity, never supplied
    by HTTP clients. No local paths, base64 images, credentials, or arbitrary
    audit JSON keys are exposed by the administrator response.
    """
    if attempt.provider_model != "kling-3.0-omni":
        return None
    root = Path(get_settings().runner_root) / "ootd-wire-audit"
    candidates = [
        attempt.intent_hash,
        f"{task.id.hex}-{variant.id.hex}-{variant.revision}",
    ]
    for identity in candidates:
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            continue
        file = root / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json")
        try:
            if not file.is_file() or file.stat().st_size > 100_000:
                continue
            record = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("externalTaskId") != identity:
            continue
        state = record.get("submissionState")
        if state not in {"PROVIDER_ACCEPTED", "PROVIDER_RECOVERED"}:
            continue
        # A different provider receipt must never be paired with this clip.
        if (
            not isinstance(attempt.task_id, str)
            or record.get("providerTaskId") != attempt.task_id
        ):
            continue
        final_text = record.get("exactKlingPrompt")
        if not isinstance(final_text, str) or not 0 < len(final_text) <= 3072:
            continue
        image_status = record.get("imageReferenceConversion")
        native_status = record.get("nativeShotConversion")
        return {
            "evidence": "provider_accepted_wire_snapshot",
            "recordedAtUtc": record.get("recordedAtUtc"),
            "submissionState": state,
            "imageReferenceConversion": image_status if isinstance(image_status, str) else None,
            "imageReferenceSourceConversion": (
                record.get("imageReferenceSourceConversion")
                if isinstance(record.get("imageReferenceSourceConversion"), str) else None
            ),
            "imageReferenceSourceAliasesChanged": (
                record.get("imageReferenceSourceAliasesChanged")
                if type(record.get("imageReferenceSourceAliasesChanged")) is int else None
            ),
            "imageReferenceBindingVerified": (
                record.get("imageReferenceBindingVerified")
                if isinstance(record.get("imageReferenceBindingVerified"), bool) else None
            ),
            "nativeShotConversion": native_status if isinstance(native_status, str) else None,
            "exactKlingPrompt": final_text,
        }
    return None


async def get_admin_video_trace(session: AsyncSession, artifact_id: uuid.UUID) -> dict:
    row = (await session.execute(
        select(DownloadArtifact, GenerationAttempt, GenerationVariant, ParentTask)
        .join(GenerationAttempt, GenerationAttempt.id == DownloadArtifact.attempt_id)
        .join(GenerationVariant, GenerationVariant.id == GenerationAttempt.variant_id)
        .join(ParentTask, ParentTask.id == GenerationVariant.parent_task_id)
        .where(
            DownloadArtifact.id == artifact_id,
            DownloadArtifact.status == DownloadStatus.READY,
            DownloadArtifact.object_key.is_not(None),
            DownloadArtifact.deleted_at.is_(None),
        )
    )).first()
    if not row:
        raise HTTPException(404, "saved video not found")
    artifact, attempt, variant, task = row

    wire_audit = _confirmed_wire_audit(attempt, variant, task)
    config = variant.generation_config_json or {}
    settings = task.settings_json or {}
    if not isinstance(config, dict):
        config = {}
    if not isinstance(settings, dict):
        settings = {}
    compiled = task.compiled_plan_json or {}
    seed = variant.variant_seed_json or {}
    if not isinstance(compiled, dict):
        compiled = {}
    if not isinstance(seed, dict):
        seed = {}
    plans = compiled.get("variants") or []
    index = variant.variant_index
    if index is None:
        index = 1 if _enum(variant.variant_type) == "STABLE" else 2
    plan = _safe_plan_item(plans[index - 1]) if (
        isinstance(plans, list) and 0 < index <= len(plans)
    ) else {}
    bindings = config.get("referenceImageBindings")
    binding_items = [
        item for item in bindings
        if isinstance(item, dict) and isinstance(item.get("assetId"), str)
    ] if isinstance(bindings, list) else []
    # The ordered historical binding is more authoritative than all task assets.
    bound_ids = {str(v["assetId"]): v for v in binding_items}
    task_assets = list(await session.scalars(
        select(Asset).where(Asset.parent_task_id == task.id)
        .order_by(Asset.order_no, Asset.id)
    ))
    assets = []
    for asset in task_assets:
        bound = bound_ids.get(str(asset.id))
        if binding_items and not bound:
            continue
        if not asset.used_for_generation:
            continue
        if asset.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        if not binding_items and not asset.used_for_generation:
            continue
        assets.append({
            "id": str(asset.id),
            "filename": asset.original_filename,
            "role": bound.get("role") if bound else asset.role,
            "imageLabel": bound.get("imageId") if bound else None,
            "order": asset.order_no,
            "usedForGeneration": bool(asset.used_for_generation),
            "bindingEvidence": "saved_binding" if bound else "task_asset_only",
            "available": asset.deleted_at is None and bool(asset.object_key),
        })
    if binding_items:
        rank = {str(item["assetId"]): n for n, item in enumerate(binding_items)}
        assets.sort(key=lambda a: rank.get(a["id"], 999))

    commands = (await session.execute(
        select(GenerationCommand).where(GenerationCommand.attempt_id == attempt.id)
    )).scalars().all()
    command = commands[0] if commands else None
    submissions = (await session.scalars(
        select(GenerationSubmissionAttempt)
        .where(GenerationSubmissionAttempt.attempt_id == attempt.id)
        .order_by(GenerationSubmissionAttempt.sequence)
    )).all()
    op_rows = (await session.execute(
        select(MaterializationOperation)
        .join(MaterializationSaga, MaterializationSaga.id == MaterializationOperation.saga_id)
        .where(MaterializationSaga.parent_task_id == task.id)
        .order_by(MaterializationOperation.started_at, MaterializationOperation.step_type)
        .limit(40)
    )).scalars().all()
    cut_rows = (await session.scalars(
        select(CutflowHandoff)
        .where(CutflowHandoff.artifact_id == artifact.id)
        .order_by(CutflowHandoff.created_at)
        .limit(8)
    )).all()
    validation_rows = (await session.scalars(
        select(ValidationResult)
        .where(ValidationResult.attempt_id == attempt.id)
        .order_by(ValidationResult.created_at)
        .limit(12)
    )).all()
    # Multi-source CUT handoffs use a separate table and reference the clip in
    # sources_json; never claim a different clip's batch as this clip's handoff.
    candidates = (await session.scalars(
        select(MultiCutflowTask)
        .where(MultiCutflowTask.user_id == task.user_id)
        .order_by(MultiCutflowTask.created_at.desc())
        .limit(1500)
    )).all()
    multi_cut_rows = [
        row for row in candidates
        if isinstance(row.sources_json, list)
        and any(
            isinstance(source, dict)
            and (
                str(source.get("artifactId")) == str(artifact.id)
                or str(source.get("attemptId")) == str(attempt.id)
            )
            for source in row.sources_json
        )
    ]
    audit_rows = (await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_id.in_([
            str(task.id), str(variant.id), str(attempt.id), str(artifact.id)
        ]))
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .limit(60)
    )).all()
    events: list[dict] = []

    def event(label: str, at: datetime | None, source: str, *, status=None,
              end: datetime | None = None, origin: str = "persisted") -> None:
        if at is not None:
            events.append({
                "label": label, "at": _iso(at), "endAt": _iso(end),
                "elapsedSeconds": _seconds(at, end),
                "status": _enum(status), "source": source, "origin": origin,
            })

    event("任务记录创建", task.created_at, "parent_task.created_at")
    event("本次片段记录创建", variant.created_at, "generation_variant.created_at")
    event("分析开始（最近记录）", task.analysis_started_at,
          "parent_task.analysis_started_at")
    event("人工/自动确认方案", variant.confirmed_at, "generation_variant.confirmed_at")
    event("生成尝试进入系统", attempt.created_at, "generation_attempt.created_at")
    if command is not None:
        event("进入执行命令队列", command.created_at, "generation_command.created_at",
              status=command.status)
        event("执行命令启动", command.launch_started_at,
              "generation_command.launch_started_at")
    event("本地启动生成", attempt.launch_started_at,
          "generation_attempt.launch_started_at")
    event("供应商提交请求开始", attempt.submission_started_at,
          "generation_attempt.submission_started_at",
          end=attempt.submission_finished_at)
    event("供应商确认提交", attempt.submitted_at, "generation_attempt.submitted_at",
          end=attempt.completed_at, status=attempt.provider_status)
    event("供应商生成完成", attempt.completed_at, "generation_attempt.completed_at",
          status=attempt.status)
    for item in submissions:
        event(f"第 {item.sequence} 次提交边界", item.started_at,
              "generation_submission_attempt.started_at",
              end=item.finished_at, status=item.status)
    for item in op_rows:
        event(f"素材准备：{item.step_type}", item.started_at,
              "materialization_operation.started_at",
              end=item.completed_at, status=item.status)
    event("保存原片记录创建", artifact.created_at, "download_artifact.created_at")
    # updated_at is the last saved-record update; not proof of download completion.
    event("保存原片记录最后更新", artifact.updated_at,
          "download_artifact.updated_at", status=artifact.status)
    for item in validation_rows:
        event("视频校验记录", item.created_at,
              "validation_result.created_at", status=item.status)
    for item in multi_cut_rows:
        event("CUT 多源送剪记录创建", item.created_at,
              "multi_cutflow_task.created_at", status=item.status)
        event("CUT 多源送剪状态最后更新", item.updated_at,
              "multi_cutflow_task.updated_at", status=item.status)
    for item in cut_rows:
        event("CUT 送剪记录创建", item.created_at, "cutflow_handoff.created_at",
              status=item.status)
        event("CUT 送剪记录最后更新", item.updated_at,
              "cutflow_handoff.updated_at", status=item.status)
    for item in audit_rows:
        event(f"操作日志：{item.action}", item.created_at,
              "audit_event.created_at", status=item.to_state)
    events.sort(key=lambda item: item["at"])

    durations = [
        {
            "label": "任务创建至原片记录创建（总跨度）",
            "seconds": _seconds(task.created_at, artifact.created_at),
            "confidence": "boundary",
        },
        {
            "label": "方案确认至供应商提交（含准备与排队）",
            "seconds": _seconds(variant.confirmed_at, attempt.submitted_at),
            "confidence": "boundary",
        },
        {
            "label": "供应商提交请求耗时",
            "seconds": _seconds(attempt.submission_started_at,
                                attempt.submission_finished_at),
            "confidence": "measured",
        },
        {
            "label": "云端生成至完成（含轮询）",
            "seconds": _seconds(attempt.submitted_at, attempt.completed_at),
            "confidence": "boundary",
        },
        {
            "label": "云端完成至原片记录建立",
            "seconds": _seconds(attempt.completed_at, artifact.created_at),
            "confidence": "boundary",
        },
        {
            "label": "分析累计耗时（系统记录）",
            "seconds": task.analysis_duration_seconds,
            "confidence": "aggregate",
        },
        {
            "label": "提示词分析累计耗时（系统记录）",
            "seconds": task.prompt_analysis_duration_seconds,
            "confidence": "aggregate",
        },
        {
            "label": "上传耗时（客户端记录）",
            "seconds": task.source_upload_duration_seconds,
            "confidence": "client_reported",
        },
    ]
    return {
        "artifactId": str(artifact.id),
        "taskId": str(task.id),
        "attemptId": str(attempt.id),
        "variantId": str(variant.id),
        "klingWireAudit": wire_audit,
        "taskCreatedAt": _iso(task.created_at),
        "prompt": {
            "savedText": variant.prompt_text or None,
            "savedHash": variant.prompt_hash,
            "provenance": "saved_variant_text_not_immutable_submission_snapshot",
            "plan": plan,
            "planner": _text(seed.get("planner"), 100),
            "compiledBy": _text(compiled.get("compiler"), 100),
        },
        "direction": {
            "generationMode": config.get("generationMode") or settings.get("generationMode"),
            "directorMode": config.get("directorMode") or settings.get("directorMode"),
            "cutMode": config.get("cutMode") or settings.get("cutMode"),
            "smartStoryboard": config.get("smartStoryboard"),
            "multiShot": config.get("multiShot"),
            "durationSeconds": config.get("duration") or config.get("durationSeconds"),
            "model": attempt.provider_model,
            "provider": attempt.provider,
            "providerBackend": config.get("providerBackend"),
            "variantIndex": variant.variant_index,
            "referenceCount": len(assets),
        },
        "references": assets,
        "timeline": events,
        "durations": durations,
        "taskStatus": _enum(task.phase),
        "attemptStatus": _enum(attempt.status),
        "downloadStatus": _enum(artifact.status),
        "cut": [
            *[{
                "kind": "single",
                "status": _enum(item.status),
                "createdAt": _iso(item.created_at),
                "updatedAt": _iso(item.updated_at),
                "errorCode": item.error_code,
                "batchId": item.cutflow_batch_id,
            } for item in cut_rows],
            *[{
                "kind": "multi",
                "status": _enum(item.status),
                "createdAt": _iso(item.created_at),
                "updatedAt": _iso(item.updated_at),
                "errorCode": item.error_code,
                "batchId": str(item.id),
            } for item in multi_cut_rows],
        ],
        "validation": [{
            "status": _enum(item.status),
            "coverage": item.coverage,
            "issueCodes": item.issue_codes_json or [],
            "createdAt": _iso(item.created_at),
        } for item in validation_rows],
        "limitations": [
            "历史提示词是当前保存的版本；没有已核实的供应商提交快照时不能确认与实际发送内容逐字一致。",
            "时间线仅列出真实保存的事件，未记录的节点不推测，也不以最后更新时间冒充完成时间。",
            "素材绑定优先使用本次变体保存的 referenceImageBindings；"
            "缺少绑定时仅展示当时任务内标记用于生成的图片。",
            "CUT 送剪状态与时间来自已保存的交接记录，不代表最终成片一定完成。",
        ],
    }


async def get_admin_video_reference(
    session: AsyncSession, artifact_id: uuid.UUID, asset_id: uuid.UUID
) -> Asset:
    """Allow only an image bound to the exact task/variant under inspection."""
    row = (await session.execute(
        select(Asset, GenerationVariant)
        .join(ParentTask, ParentTask.id == Asset.parent_task_id)
        .join(GenerationVariant, GenerationVariant.parent_task_id == ParentTask.id)
        .join(GenerationAttempt, GenerationAttempt.variant_id == GenerationVariant.id)
        .join(DownloadArtifact, DownloadArtifact.attempt_id == GenerationAttempt.id)
        .where(
            Asset.id == asset_id,
            DownloadArtifact.id == artifact_id,
            DownloadArtifact.status == DownloadStatus.READY,
            DownloadArtifact.deleted_at.is_(None),
            Asset.deleted_at.is_(None),
            Asset.used_for_generation.is_(True),
        )
    )).first()
    if row is None:
        raise HTTPException(404, "reference image not found")
    asset, variant = row
    bindings = (variant.generation_config_json or {}).get("referenceImageBindings") or []
    if isinstance(bindings, list) and bindings and str(asset.id) not in {
        item.get("assetId") for item in bindings if isinstance(item, dict)
    }:
        raise HTTPException(404, "reference image not bound to inspected clip")
    if asset.mime_type not in {"image/jpeg", "image/png", "image/webp"} or not asset.object_key:
        raise HTTPException(404, "reference image unavailable")
    return asset
