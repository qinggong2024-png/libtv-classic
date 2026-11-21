"""Stable edited-video download names, independent of generation and CUT receipts."""

import hashlib
import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from libtv_agent.domain.models import (
    ApiIdempotencyRecord,
    CutflowHandoff,
    MultiCutflowTask,
    ParentTask,
)
from libtv_agent.services.brand_identity import brand_identity
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

_TENANT = "classic-cutflow-downloads"
_OPERATION = "cutflow-download-name"
_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _brand_code(source: dict) -> str | None:
    reference = source.get("brandReference") or source.get("brand_reference") or {}
    for value in (
        reference.get("profileId"),
        source.get("siteProfileId"),
        source.get("site_profile_id"),
        reference.get("shortName"),
        reference.get("fullName"),
        reference.get("accountBrandName"),
    ):
        if isinstance(value, str) and (identity := brand_identity(value)):
            return identity.short_name
    # Preserve an explicit custom abbreviation, never infer initials from a title.
    short_name = reference.get("shortName")
    if isinstance(short_name, str) and re.fullmatch(r"[A-Za-z0-9]{1,20}", short_name):
        return short_name.upper()
    return None


async def _output_brand(session: AsyncSession, row) -> str | None:
    if isinstance(row, CutflowHandoff):
        source = (row.manifest_json or {}).get("source") or {}
        if code := _brand_code(source):
            return code
        task = await session.get(ParentTask, row.parent_task_id)
        if task:
            site = (task.settings_json or {}).get("cutflowSite") or {}
            return _brand_code({**site, "siteProfileId": task.site_profile_id})
        return None
    sources = row.sources_json or []
    codes = [_brand_code(source) for source in sources]
    if codes and all(codes) and len(set(codes)) == 1:
        return codes[0]
    return None


def _completion_day(output: dict, row) -> str:
    value = output.get("created_at")
    try:
        completed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        completed = row.updated_at or row.created_at
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)
    return completed.astimezone(_TIMEZONE).strftime("%y%m%d")


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def download_filename(
    session: AsyncSession, row: CutflowHandoff | MultiCutflowTask, output: dict,
) -> str:
    """Reserve once on first download; retries, users and processes share one ledger.

    The existing migration-created idempotency table supplies an atomic compare-and-
    swap ledger per brand/completion day. No schema or source-output mutation.
    """
    output_id = str(output["id"])
    brand = await _output_brand(session, row)
    if not brand:
        # Old imports without verifiable brand data keep their established name.
        return f"edited-{output_id}.mp4"
    day = _completion_day(output, row)
    scope = f"{brand}:{day}"
    kind = "single" if isinstance(row, CutflowHandoff) else "multi"
    identity = f"{kind}:{row.id}:{output_id}"
    # Capture ORM attributes before any rollback, which expires loaded instances.
    for _ in range(12):
        record = (await session.execute(select(
            ApiIdempotencyRecord.payload_hash, ApiIdempotencyRecord.response_body_json,
        ).where(
            ApiIdempotencyRecord.tenant_id == _TENANT,
            ApiIdempotencyRecord.operation_type == _OPERATION,
            ApiIdempotencyRecord.idempotency_key == scope,
        ))).first()
        ledger = record.response_body_json if record else {"nextSequence": 1, "outputs": {}}
        if filename := ledger["outputs"].get(identity):
            return filename
        sequence = ledger["nextSequence"]
        filename = f"PMO-{brand}{day}AA{sequence:02d}.mp4"
        updated = {
            "nextSequence": sequence + 1,
            "outputs": {**ledger["outputs"], identity: filename},
        }
        try:
            if record:
                result = await session.execute(update(ApiIdempotencyRecord).where(
                    ApiIdempotencyRecord.tenant_id == _TENANT,
                    ApiIdempotencyRecord.operation_type == _OPERATION,
                    ApiIdempotencyRecord.idempotency_key == scope,
                    ApiIdempotencyRecord.payload_hash == record.payload_hash,
                ).values(
                    payload_hash=_digest(updated), response_body_json=updated,
                ).execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    await session.rollback()
                    continue
            else:
                session.add(ApiIdempotencyRecord(
                    tenant_id=_TENANT, operation_type=_OPERATION, idempotency_key=scope,
                    payload_hash=_digest(updated), response_status=200,
                    response_body_json=updated, created_at=datetime.now(UTC),
                    expires_at=datetime(9999, 1, 1, tzinfo=UTC),
                ))
            await session.commit()
            return filename
        except IntegrityError:
            await session.rollback()
        except OperationalError as exc:
            await session.rollback()
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
    raise RuntimeError("成片命名暂不可用，请稍后重新下载")
