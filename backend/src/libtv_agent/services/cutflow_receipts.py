"""Classic-owned import receipts written after a verified PENDING_REVIEW result."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import CutflowHandoff, MultiCutflowTask
from libtv_agent.services.cutflow_handoff import (
    CutflowHandoffError,
    safe_path,
    sha256_file,
    write_json_atomic,
)
from sqlalchemy import select, update

RECEIPT_SCHEMA = "yiying-classic-import/1"
SAFE_RECEIPT_KEY = re.compile(r"^(?:handoff|multi)-[0-9a-f-]{36}$")


def _timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        when = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return when.isoformat()
    return None


def receipt_relative_path(handoff_key: str) -> str:
    if not SAFE_RECEIPT_KEY.fullmatch(handoff_key):
        raise CutflowHandoffError("INVALID_RESULT", "经典版回执标识无效")
    return f"receipts/classic/{handoff_key}.json"


def _public_output(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": output.get("id"),
        "sha256": output.get("sha256"),
        "sizeBytes": output.get("size_bytes"),
        "durationSeconds": output.get("duration_seconds"),
        "width": output.get("width"),
        "height": output.get("height"),
        "reviewStatus": output.get("review_status"),
        "importedAt": output.get("created_at"),
    }


def build_receipt(
    settings: Settings,
    *,
    handoff_key: str,
    record_id: str,
    contract_version: str,
    manifest_sha256: str,
    batch_id: str | None,
    outputs: list[dict[str, Any]],
    source_count: int,
    source_clip_ids: list[str],
    verified_at: str,
) -> dict[str, Any]:
    build_id = os.environ.get("LIBTV_CLASSIC_BUILD_ID")
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "writer": "classic",
        "status": "PENDING_REVIEW",
        "reviewStatus": "PENDING",
        "handoffKey": handoff_key,
        "taskId": record_id,
        "contractVersion": contract_version,
        "manifestSha256": manifest_sha256,
        "batchId": batch_id,
        "sourceCount": source_count,
        "sourceClipIds": source_clip_ids,
        "outputs": [_public_output(output) for output in outputs],
        "verifiedAt": verified_at,
        "classic": {
            "appEnv": settings.app_env,
            "buildId": build_id or "unconfigured",
        },
    }


def write_receipt(
    settings: Settings,
    *,
    handoff_key: str,
    record_id: str,
    contract_version: str,
    manifest_sha256: str,
    batch_id: str | None,
    outputs: list[dict[str, Any]],
    source_count: int,
    source_clip_ids: list[str],
    verified_at: str,
) -> str:
    if settings.cutflow_handoff_root is None:
        raise CutflowHandoffError("CUTFLOW_UNAVAILABLE", "共享回执目录未配置")
    relative = receipt_relative_path(handoff_key)
    destination = safe_path(Path(settings.cutflow_handoff_root), relative)
    payload = build_receipt(
        settings,
        handoff_key=handoff_key,
        record_id=record_id,
        contract_version=contract_version,
        manifest_sha256=manifest_sha256,
        batch_id=batch_id,
        outputs=outputs,
        source_count=source_count,
        source_clip_ids=source_clip_ids,
        verified_at=verified_at,
    )
    if destination.is_file():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing == payload:
            return sha256_file(destination)
    return write_json_atomic(destination, payload)


async def ensure_receipts(
    database: Database,
    settings: Settings,
    *,
    limit: int = 50,
) -> int:
    """Retry missing reverse receipts without changing business-task state."""

    if settings.cutflow_handoff_root is None:
        return 0
    async with database.session_factory() as session:
        single_rows = list(
            await session.scalars(
                select(CutflowHandoff)
                .where(CutflowHandoff.status == "PENDING_REVIEW")
                .order_by(CutflowHandoff.updated_at.desc())
                .limit(limit)
            )
        )
        multi_rows = list(
            await session.scalars(
                select(MultiCutflowTask)
                .where(MultiCutflowTask.status == "PENDING_REVIEW")
                .order_by(MultiCutflowTask.updated_at.desc())
                .limit(limit)
            )
        )

    written = 0
    for row in single_rows:
        manifest = dict(row.manifest_json or {})
        digest = manifest.get("manifestSha256")
        outputs = list(row.outputs_json or [])
        if not isinstance(digest, str) or not outputs:
            continue
        relative = receipt_relative_path(row.handoff_key)
        destination = safe_path(Path(settings.cutflow_handoff_root), relative)
        if manifest.get("_classicReceiptSha256") and destination.is_file():
            continue
        try:
            receipt_hash = await asyncio.to_thread(
                write_receipt,
                settings,
                handoff_key=row.handoff_key,
                record_id=str(row.id),
                contract_version=row.contract_version,
                manifest_sha256=digest,
                batch_id=row.cutflow_batch_id,
                outputs=outputs,
                source_count=1,
                source_clip_ids=[str(row.variant_id)],
                verified_at=_timestamp(row.updated_at) or datetime.now(UTC).isoformat(),
            )
        except (OSError, CutflowHandoffError, ValueError):
            continue
        manifest["_classicReceiptSha256"] = receipt_hash
        async with database.session_factory() as session:
            await session.execute(
                update(CutflowHandoff)
                .where(
                    CutflowHandoff.id == row.id,
                    CutflowHandoff.status == "PENDING_REVIEW",
                )
                .values(manifest_json=manifest)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        written += 1

    for row in multi_rows:
        manifest = dict(row.manifest_json or {})
        digest = manifest.get("manifestSha256")
        outputs = list(row.output_json or [])
        if not isinstance(digest, str) or not outputs:
            continue
        handoff_key = f"multi-{row.id}"
        relative = receipt_relative_path(handoff_key)
        destination = safe_path(Path(settings.cutflow_handoff_root), relative)
        if manifest.get("_classicReceiptSha256") and destination.is_file():
            continue
        clip_ids = [
            str(source["clipId"])
            for source in row.sources_json or []
            if isinstance(source, dict) and source.get("clipId")
        ]
        try:
            receipt_hash = await asyncio.to_thread(
                write_receipt,
                settings,
                handoff_key=handoff_key,
                record_id=str(row.id),
                contract_version=str(manifest.get("contractVersion") or "legacy"),
                manifest_sha256=digest,
                batch_id=manifest.get("_cutBatchId"),
                outputs=outputs,
                source_count=len(row.sources_json or []),
                source_clip_ids=clip_ids,
                verified_at=_timestamp(row.updated_at) or datetime.now(UTC).isoformat(),
            )
        except (OSError, CutflowHandoffError, ValueError):
            continue
        manifest["_classicReceiptSha256"] = receipt_hash
        async with database.session_factory() as session:
            await session.execute(
                update(MultiCutflowTask)
                .where(
                    MultiCutflowTask.id == row.id,
                    MultiCutflowTask.status == "PENDING_REVIEW",
                )
                .values(manifest_json=manifest)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        written += 1
    return written
