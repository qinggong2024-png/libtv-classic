"""Fail-closed CUT multi-source status polling and output import support.

This worker handles only already-persisted MultiCutflowTask rows. It never
creates a remote task; once an identity exists it only polls that task UUID.
Runtime dispatch stays disabled unless this classic instance explicitly enables
the LAN multi-source capability and has complete CUT configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from libtv_agent.adapters.object_store import ObjectStore
from libtv_agent.config import Settings
from libtv_agent.db import Database
from libtv_agent.domain.models import MultiCutflowTask
from libtv_agent.services import cutflow_observation
from libtv_agent.services.account_access import settle_usd
from libtv_agent.services.cutflow_handoff import (
    CutflowHandoffError,
    configured,
    copy_verified,
    import_outputs,
    require_configuration,
    safe_path,
    sha256_file,
    write_json_atomic,
)
from libtv_agent.services.cutflow_multi_adapter import (
    MULTI_STATUS_MAP,
    build_multi_manifest,
    cut_multi_request,
    normalize_cut_multi_response,
)
from libtv_agent.services.cutflow_product_package import NATIVE_ROOT
from libtv_agent.services.cutflow_protocol import (
    JOB_CONTRACT,
    check_status_revision,
    remember_status_revision,
)
from sqlalchemy import or_, select, update

MAX_RESPONSE_BYTES = 1_048_576
OUTPUT_PATH_PREFIX = "outbox"
STATUS_PATH = "status.json"
NOTIFICATION_REPEAT_SECONDS = 120
NOTIFICATION_ERROR_REPEAT_SECONDS = 60
NOTIFICATION_REJECT_REPEAT_SECONDS = 300
TASK_PACKAGE_SAFE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MultiControlHTTPError(CutflowHandoffError):
    def __init__(self, status_code: int, detail: str = ""):
        message = f"CUT 多源状态接口返回 HTTP {status_code}"
        # Keep a short, value-free excerpt: a bare status code makes a 409
        # impossible to diagnose without another round trip to the other side.
        if detail:
            message = f"{message}（{detail[:120]}）"
        super().__init__("CUTFLOW_HTTP_ERROR", message)
        self.status_code = status_code
        self.detail = detail


def _aware(value):
    """SQLite returns naive datetimes for timezone=True columns; normalise."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _http_detail(exc: HTTPError) -> str:
    """Only expose CUT's explicit safe error code, never an arbitrary response body."""
    try:
        payload = json.loads(exc.read(512))
    except (ValueError, UnicodeDecodeError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    reason = payload.get("reason") or payload.get("error")
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", reason):
        return reason
    return ""


def multi_worker_ready(settings: Settings) -> bool:
    return bool(
        settings.cutflow_multi_source_enabled
        and settings.cutflow_transport == "lan"
        and settings.cutflow_nas_status_enabled
        and configured(settings)
    )


def cut_multi_status_request(settings: Settings, task_id: uuid.UUID | str) -> dict:
    """Query exactly one persisted CUT task; this helper never submits work."""
    require_configuration(settings)
    task_uuid = uuid.UUID(str(task_id))
    url = (
        f"{settings.cutflow_base_url.rstrip('/')}/api/integrations/"
        f"libtv-multisource/{quote(str(task_uuid), safe='')}"
    )
    request = Request(
        url,
        headers={
            **(
                {"Authorization": f"Bearer {settings.cutflow_service_token}"}
                if settings.cutflow_service_token
                else {}
            ),
            "Accept": "application/json",
            "Cache-Control": "no-store",
        },
        method="GET",
    )
    try:
        with build_opener(NoRedirect()).open(
            request,
            timeout=settings.cutflow_request_timeout_seconds,
        ) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise MultiControlHTTPError(exc.code, _http_detail(exc)) from None
    except (URLError, TimeoutError, ConnectionError):
        raise
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态响应过大")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态响应格式错误") from None
    if not isinstance(result, dict):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态响应格式错误")
    return result


def cut_multi_submit_request(settings: Settings, payload: dict) -> dict:
    """POST an immutable, already-published package using the stable task key."""
    require_configuration(settings)
    url = f"{settings.cutflow_base_url.rstrip('/')}/api/integrations/libtv-multisource"
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=raw,
        headers={
            **(
                {"Authorization": f"Bearer {settings.cutflow_service_token}"}
                if settings.cutflow_service_token
                else {}
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(
            request,
            timeout=settings.cutflow_request_timeout_seconds,
        ) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise MultiControlHTTPError(exc.code, _http_detail(exc)) from None
    except (URLError, TimeoutError, ConnectionError):
        raise
    if len(body) > MAX_RESPONSE_BYTES:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源接单响应过大")
    try:
        result = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源接单响应格式错误") from None
    if not isinstance(result, dict):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源接单响应格式错误")
    return result


async def _copy_native_media(
    staging: Path, source: Path, snapshot: dict, max_bytes: int,
) -> None:
    relative = snapshot.get("nativeRelativePath")
    if relative is None:
        return  # Immutable snapshots from before native folder export.
    if not isinstance(relative, str) or not relative.startswith(f"{NATIVE_ROOT}/"):
        raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "CUT 商品目录路径无效")
    target = safe_path(staging, relative)
    if target.exists():
        # Sibling variants share one physical native product image, while
        # references/ retains a distinct binding for each source in LAN /2.
        size = target.stat().st_size
        digest = await asyncio.to_thread(sha256_file, target)
        if size != snapshot["sizeBytes"] or digest != snapshot["sha256"]:
            raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "CUT 商品目录文件名冲突")
        return
    size = await asyncio.to_thread(
        copy_verified, source, target, snapshot["sha256"], max_bytes,
    )
    if size != snapshot["sizeBytes"]:
        raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "CUT 商品目录素材大小不匹配")


async def publish_multi_package(
    task: MultiCutflowTask,
    settings: Settings,
    object_store: ObjectStore,
) -> tuple[dict, str]:
    """Copy verified source snapshots, then atomically publish manifest and READY."""
    root = settings.cutflow_handoff_root
    if root is None:
        raise CutflowHandoffError("CUTFLOW_UNAVAILABLE", "多源共享目录未配置")
    manifest = build_multi_manifest(task)
    task_id = str(uuid.UUID(str(task.id)))
    inbox = safe_path(root, "inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    package_name = f"multi-{task_id}"
    final = safe_path(root, f"inbox/{package_name}")
    # Keep the staging name SHORT. The final name plus `sources/<clip>.mp4` plus
    # copy_verified's `.<uuid>.part` suffix already sits close to the Windows
    # 260-character limit on a long UNC share; a 79-character staging name
    # (`.multi-<uuid>.<32hex>.tmp`) pushed a real run over it and every copy
    # failed with FileNotFoundError. A 13-character token keeps ~65 chars spare.
    staging = safe_path(root, f"inbox/.m-{uuid.uuid4().hex[:12]}.tmp")
    staging.mkdir(parents=True, exist_ok=False)
    local_files: list[object] = []
    try:
        for source in sorted(task.sources_json, key=lambda item: item["order"]):
            object_key = source.get("objectKey")
            expected_hash = source.get("sha256")
            expected_size = source.get("sizeBytes")
            relative = source.get("relativePath")
            if (
                not isinstance(object_key, str)
                or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or not isinstance(expected_size, int)
                or expected_size < 1
                or not isinstance(relative, str)
                or not relative.startswith("sources/")
            ):
                raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "多源素材快照不完整")
            local = (
                settings.artifact_root / "cutflow-cache" / f"multi-{task_id}-{source['order']}.mp4"
            )
            local.parent.mkdir(parents=True, exist_ok=True)
            local_files.append(local)
            resolved = await object_store.localize(object_key, local)
            copied_size = await asyncio.to_thread(
                copy_verified,
                resolved,
                safe_path(staging, relative),
                expected_hash,
                settings.cutflow_max_media_bytes,
            )
            if copied_size != expected_size:
                raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "多源素材大小与快照不一致")
            await _copy_native_media(staging, resolved, source, settings.cutflow_max_media_bytes)

            # Ship the task's real product images so the receiver's quality gate
            # can verify against actual product references instead of a faked or
            # brand-only substitute.
            for reference in source.get("productReferences") or []:
                ref_key = reference.get("objectKey")
                ref_hash = reference.get("sha256")
                ref_size = reference.get("sizeBytes")
                ref_relative = reference.get("relativePath")
                if (
                    not isinstance(ref_key, str)
                    or not isinstance(ref_hash, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", ref_hash)
                    or not isinstance(ref_size, int)
                    or ref_size < 1
                    or not isinstance(ref_relative, str)
                    or not ref_relative.startswith("references/")
                ):
                    raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "商品参考快照不完整")
                ref_local = (
                    settings.artifact_root / "cutflow-cache"
                    / f"multi-{task_id}-ref-{reference['refId']}{Path(ref_relative).suffix}"
                )
                ref_local.parent.mkdir(parents=True, exist_ok=True)
                local_files.append(ref_local)
                ref_resolved = await object_store.localize(ref_key, ref_local)
                ref_copied = await asyncio.to_thread(
                    copy_verified,
                    ref_resolved,
                    safe_path(staging, ref_relative),
                    ref_hash,
                    settings.cutflow_max_media_bytes,
                )
                if ref_copied != ref_size:
                    raise CutflowHandoffError("CUTFLOW_SOURCE_INVALID", "商品参考大小与快照不一致")
                await _copy_native_media(
                    staging, ref_resolved, reference, settings.cutflow_max_media_bytes,
                )

        raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        manifest_path = safe_path(staging, "manifest.json")
        with manifest_path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        write_json_atomic(staging / "READY", {"sha256": digest})
        if final.exists():
            existing_manifest = safe_path(final, "manifest.json")
            existing_digest = (
                hashlib.sha256(existing_manifest.read_bytes()).hexdigest()
                if existing_manifest.is_file()
                else None
            )
            if existing_digest != digest:
                raise CutflowHandoffError("IMMUTABLE_RESULT_CHANGED", "多源任务包已存在且内容不同")
            media = []
            for source in task.sources_json:
                for item in [source, *(source.get("productReferences") or [])]:
                    media.append(item)
                    if item.get("nativeRelativePath"):
                        media.append({**item, "relativePath": item["nativeRelativePath"]})
            for item in media:
                existing_source = safe_path(final, item["relativePath"])
                existing_hash = (
                    await asyncio.to_thread(sha256_file, existing_source)
                    if existing_source.is_file()
                    else None
                )
                if (
                    not existing_source.is_file()
                    or existing_source.stat().st_size != item["sizeBytes"]
                    or existing_hash != item["sha256"]
                ):
                    raise CutflowHandoffError(
                        "CUTFLOW_SOURCE_INVALID",
                        "已发布多源素材包缺少文件或校验不匹配",
                    )
            # The immutable manifest and every source have been verified.
            # Recreate only a LOST READY marker, never the existing package.
            # Without READY, CUT's durable scanner cannot discover this task.
            existing_ready = safe_path(final, "READY")
            if existing_ready.is_file():
                try:
                    ready_marker = json.loads(existing_ready.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise CutflowHandoffError(
                        "IMMUTABLE_RESULT_CHANGED",
                        "已发布的 READY 标记损坏，不能猜测其内容",
                    ) from exc
                if not isinstance(ready_marker, dict) or ready_marker.get("sha256") != digest:
                    raise CutflowHandoffError(
                        "IMMUTABLE_RESULT_CHANGED",
                        "已发布的 READY 标记与原始任务清单不符",
                    )
            elif existing_ready.exists():
                raise CutflowHandoffError(
                    "IMMUTABLE_RESULT_CHANGED", "已发布的 READY 路径不是文件",
                )
            else:
                # Same task ID + exact manifest hash + verified media => safe
                # atomic repair, not a new CUT task or provider submission.
                write_json_atomic(existing_ready, {"sha256": digest})
            # The exact immutable package was published by an earlier attempt.
            return (
                {
                    **manifest,
                    "manifestSha256": digest,
                    "manifestRelativePath": f"inbox/{package_name}/manifest.json",
                },
                digest,
            )
        os.replace(staging, final)
        return (
            {
                **manifest,
                "manifestSha256": digest,
                "manifestRelativePath": f"inbox/{package_name}/manifest.json",
            },
            digest,
        )
    finally:
        for local in local_files:
            await asyncio.to_thread(local.unlink, missing_ok=True)
        if staging.exists():
            import shutil

            await asyncio.to_thread(shutil.rmtree, staging, True)


def validate_multi_output_paths(
    task_id: uuid.UUID | str,
    outputs: list[dict],
    nas_root,
) -> list[dict]:
    """Require each result path to be a file directly inside this task outbox."""
    task = str(uuid.UUID(str(task_id)))
    if not TASK_PACKAGE_SAFE_ID.fullmatch(task):
        raise CutflowHandoffError("INVALID_TASK_ID", "多源任务编号无效")
    task_root = safe_path(nas_root, f"{OUTPUT_PATH_PREFIX}/{task}")
    normalized = []
    for item in outputs:
        relative = item.get("relative_path")
        if not isinstance(relative, str):
            raise CutflowHandoffError("UNSAFE_PATH", "CUT 多源成片路径无效")
        parts = relative.split("/")
        if (
            len(parts) != 3
            or parts[0] != OUTPUT_PATH_PREFIX
            or parts[1] != task
            or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}", parts[2])
            or parts[2] in {".", ".."}
        ):
            raise CutflowHandoffError("UNSAFE_PATH", "CUT 成片必须位于当前任务 outbox")
        # Resolve now as well as in import_outputs, rejecting symlink escapes
        # before any bytes are copied or stored.
        safe_path(task_root, parts[2])
        normalized.append({**item, "relative_path": parts[2]})
    return normalized


def normalize_multi_status(task: MultiCutflowTask, response: dict) -> dict:
    expected_hash = (task.manifest_json or {}).get("manifestSha256")
    if not isinstance(expected_hash, str):
        raise CutflowHandoffError("INVALID_RESULT", "多源任务缺少已发布清单校验值")
    required_revision = (
        (task.manifest_json or {}).get("contractVersion") == JOB_CONTRACT
    )
    result = normalize_cut_multi_response(
        response,
        task_id=task.id,
        manifest_sha256=expected_hash,
        require_revision=required_revision,
    )
    if result["id"] != str(task.id) or result["manifestSha256"] != expected_hash:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 回执与本地任务不匹配")
    if result["status"] not in set(MULTI_STATUS_MAP.values()):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态未识别")
    return result


def read_multi_nas_status(
    task: MultiCutflowTask,
    settings: Settings,
) -> dict | None:
    """Read CUT's authoritative snapshot for one persisted task."""
    if (
        not settings.cutflow_nas_status_enabled
        or settings.cutflow_transport != "lan"
        or settings.cutflow_handoff_root is None
    ):
        return None
    path = safe_path(
        settings.cutflow_handoff_root,
        f"{OUTPUT_PATH_PREFIX}/{task.id}/{STATUS_PATH}",
    )
    try:
        if path.exists() and not path.is_file():
            raise OSError("status path is not a regular file")
        with path.open("rb") as handle:
            raw = handle.read(MAX_RESPONSE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise CutflowHandoffError(
            "CUTFLOW_NAS_UNAVAILABLE",
            "CUT 共享目录暂时不可读，系统会使用同一任务编号继续重试",
        ) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态文件过大")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态文件格式错误") from None
    if not isinstance(result, dict):
        raise CutflowHandoffError("INVALID_RESULT", "CUT 多源状态文件格式错误")
    return result


async def _reconcile_terminal_cutflow_usage(session, task: MultiCutflowTask, values: dict) -> None:
    """Transition only this CUT operation's reservation after a terminal result.

    A verified and imported video is estimated consumption, not a final invoice.
    Failed tasks need a billing verdict, so do not silently release their funds.
    In-flight, missing-receipt and transport errors retain their reservation.
    The caller MUST run this only after successfully fencing the same task lease,
    inside the same database transaction as the task status update.
    """
    state = values.get("status")
    if state == "PENDING_REVIEW" and values.get("output_json"):
        await settle_usd(session, kind="CUTFLOW", operation_id=task.id, status="ESTIMATED")
    elif state == "FAILED":
        await settle_usd(session, kind="CUTFLOW", operation_id=task.id, status="UNCERTAIN")


async def _write_claimed(
    database: Database,
    task: MultiCutflowTask,
    token: str,
    values: dict,
) -> bool:
    async with database.session_factory() as session:
        written = await session.execute(
            update(MultiCutflowTask)
            .where(
                MultiCutflowTask.id == task.id,
                MultiCutflowTask.user_id == task.user_id,
                MultiCutflowTask.lease_token == token,
            )
            .values(**values, lease_token=None, lease_until=None)
            .execution_options(synchronize_session=False)
        )
        if written.rowcount == 1:
            await _reconcile_terminal_cutflow_usage(session, task, values)
        await session.commit()
        return written.rowcount == 1


async def import_multi_outputs(
    task: MultiCutflowTask,
    result: dict,
    settings: Settings,
    object_store: ObjectStore,
) -> list[dict]:
    """Verify and import outputs using the established single-source validator.

    The shim preserves the existing FFmpeg decode, size, hash and technical
    validation path while directing storage and NAS reads to this task UUID.
    """
    if result.get("id") != str(task.id):
        raise CutflowHandoffError("INVALID_RESULT", "成片所属任务不匹配")
    expected_hash = (task.manifest_json or {}).get("manifestSha256")
    if not expected_hash or result.get("manifestSha256") != expected_hash:
        raise CutflowHandoffError("INVALID_RESULT", "成片清单哈希与本地任务不匹配")
    if result.get("status") not in {"PENDING_REVIEW"} or not result.get("outputs"):
        raise CutflowHandoffError("INVALID_RESULT", "待审核状态必须包含明确成片")
    normalized = validate_multi_output_paths(
        task.id,
        result["outputs"],
        settings.cutflow_handoff_root,
    )
    handoff = SimpleNamespace(
        id=task.id,
        user_id=task.user_id,
        # CUT writes to outbox/{taskId}; use the immutable UUID folder.
        handoff_key=str(task.id),
        outputs_json=list(task.output_json or []),
    )
    saved = await import_outputs(handoff, normalized, settings, object_store)
    for output in saved:
        output["task_id"] = str(task.id)
        output["manifest_sha256"] = expected_hash
    return saved


def _next_retry(failure_count: int) -> datetime:
    return datetime.now(UTC) + timedelta(
        seconds=min(300, 15 * 2 ** min(max(failure_count, 0), 5)),
    )


def _notification_repeat_seconds(notification: dict | None) -> int:
    """HTTP is a best-effort READY wake-up, never a CUT acceptance receipt."""
    if notification and notification.get("httpStatus") in {409, 413}:
        return NOTIFICATION_REJECT_REPEAT_SECONDS
    if notification and notification.get("outcome") == "failed":
        return NOTIFICATION_ERROR_REPEAT_SECONDS
    return NOTIFICATION_REPEAT_SECONDS


async def _heartbeat(database: Database, task_id: uuid.UUID, token: str) -> None:
    while True:
        await asyncio.sleep(20)
        async with database.session_factory() as session:
            await session.execute(
                update(MultiCutflowTask)
                .where(
                    MultiCutflowTask.id == task_id,
                    MultiCutflowTask.lease_token == token,
                )
                .values(lease_until=datetime.now(UTC) + timedelta(minutes=5))
                .execution_options(synchronize_session=False)
            )
            await session.commit()


async def process_multi_task(
    database: Database,
    settings: Settings,
    object_store: ObjectStore,
    task: MultiCutflowTask,
    token: str,
) -> None:
    """Poll one leased task and atomically persist its status/output snapshot."""
    try:
        manifest = task.manifest_json or {}
        if not task.manifest_path:
            descriptor, _digest = await publish_multi_package(task, settings, object_store)
            expected_path = f"inbox/multi-{task.id}/manifest.json"
            async with database.session_factory() as session:
                persisted = await session.execute(
                    update(MultiCutflowTask)
                    .where(
                        MultiCutflowTask.id == task.id,
                        MultiCutflowTask.user_id == task.user_id,
                        MultiCutflowTask.lease_token == token,
                    )
                    .values(manifest_path=expected_path, manifest_json=descriptor, status="READY")
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
                if persisted.rowcount != 1:
                    return
            task.manifest_path = expected_path
            task.manifest_json = descriptor
            task.status = "READY"
            manifest = descriptor
        expected_path = f"inbox/multi-{task.id}/manifest.json"
        digest = manifest.get("manifestSha256")
        if (
            task.manifest_path != expected_path
            or manifest.get("taskId") != str(task.id)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise CutflowHandoffError("CUTFLOW_MANIFEST_MISSING", "多源任务尚无已发布清单")
        unified = manifest.get("contractVersion") == JOB_CONTRACT
        if unified:
            response = await asyncio.to_thread(read_multi_nas_status, task, settings)
            if response is None:
                # HTTP is only a wake-up. A lost notification, rejected token or
                # temporarily unavailable CUT endpoint must not change business
                # state; the durable scanner will consume READY independently.
                # Keep NAS polling frequent. Repeated HTTP notifications are
                # rate-limited independently, never creating a fresh task ID.
                prior_notification = manifest.get("_lastNotification")
                prior_at = (
                    cutflow_observation.timestamp(prior_notification.get("at"))
                    if isinstance(prior_notification, dict) else None
                )
                age = (datetime.now(UTC) - prior_at).total_seconds() if prior_at else None
                # Once CUT's authoritative revision was verified, never POST
                # again just because its NAS snapshot temporarily disappeared.
                if (task.status in {"ACCEPTED", "IMPORTING", "EDITING", "UNKNOWN"}
                        or isinstance(manifest.get("_lastCutRevision"), int)):
                    await _write_claimed(database, task, token, {
                        "status": task.status,
                        "manifest_json": manifest,
                        "output_json": list(task.output_json or []),
                        "error_code": "CUTFLOW_STATUS_MISSING",
                        "error_message": "CUT 已接单但状态快照暂时不可读取；保持原任务并继续核对",
                        "failure_count": 0,
                        "next_run_at": datetime.now(UTC) + timedelta(
                            seconds=min(settings.cutflow_nas_scan_seconds, 300),
                        ),
                    })
                    return
                repeat_seconds = _notification_repeat_seconds(
                    prior_notification if isinstance(prior_notification, dict) else None,
                )
                skip_notification = (
                    isinstance(prior_notification, dict)
                    and age is not None and 0 <= age < repeat_seconds
                )
                if skip_notification:
                    notification = prior_notification
                else:
                    notification = {
                        "at": datetime.now(UTC).isoformat(),
                        "outcome": "seen",
                        "errorCode": None,
                        "httpStatus": None,
                    }
                    try:
                        posted = await asyncio.to_thread(
                            cut_multi_submit_request,
                            settings,
                            cut_multi_request(task, manifest),
                        )
                        if posted.get("taskId") not in {None, str(task.id)}:
                            notification.update(
                                outcome="failed",
                                errorCode="CUTFLOW_NOTIFICATION_ID_MISMATCH",
                            )
                    except Exception as exc:
                        notification.update(
                            outcome="failed",
                            errorCode=(
                                exc.code
                                if isinstance(exc, CutflowHandoffError)
                                else type(exc).__name__
                            ),
                            httpStatus=getattr(exc, "status_code", None),
                        )
                        if isinstance(exc, MultiControlHTTPError) and exc.detail:
                            notification["reason"] = exc.detail
                ready_at = manifest.get("_readyAt")
                if not isinstance(ready_at, str):
                    ready_at = datetime.now(UTC).isoformat()
                waiting_manifest = {
                    **manifest,
                    "_statusAuthority": "nas",
                    "_readyAt": ready_at,
                    "_lastNotification": notification,
                    "_lastNotificationAt": notification["at"],
                }
                ready_timestamp = cutflow_observation.timestamp(ready_at)
                waiting_seconds = (
                    (datetime.now(UTC) - ready_timestamp).total_seconds()
                    if ready_timestamp else 0
                )
                # An oversized HTTP wake-up must not erase the published NAS job.
                notification_too_large = notification.get("httpStatus") == 413
                waiting_code = (
                    "CUTFLOW_NOTIFICATION_TOO_LARGE"
                    if notification_too_large
                    else "CUTFLOW_STATUS_MISSING"
                    if waiting_seconds >= max(300, settings.cutflow_nas_scan_seconds * 5)
                    else None
                )
                rejected_reason = notification.get("reason")
                permanently_rejected = notification.get("httpStatus") == 409
                # Explicit permanent isolation of this historical task identity:
                # a new edit requires a new task ID; repeating the old package
                # cannot succeed. Preserve the immutable manifest and outputs.
                historical_isolated = (
                    permanently_rejected
                    and rejected_reason == "HISTORICAL_TASK_ISOLATED"
                    and not task.output_json
                )
                if historical_isolated:
                    await _write_claimed(
                        database, task, token,
                        {
                            "status": "FAILED",
                            "manifest_json": waiting_manifest,
                            "output_json": list(task.output_json or []),
                            "error_code": "CUTFLOW_HISTORICAL_TASK_ISOLATED",
                            "error_message": (
                                "CUT 已隔离原剪辑任务；原片、任务包与历史记录均已保留。"
                                "请在任务中心重新选择原片创建新的剪辑版本。"
                            ),
                            "failure_count": 0,
                            "next_run_at": None,
                        },
                    )
                    return
                if permanently_rejected:
                    waiting_code = "CUTFLOW_IMPORT_REJECTED"
                waiting_message = (
                    "CUT HTTP 接单通知被拒绝（413：请求体过大）。NAS 素材包已发布，"
                    "等待 CUT 扫描接单；请检查 CUT 接口大小限制，无需重新生成或重复送剪。"
                    if notification_too_large else
                    f"CUT 拒绝接单（{rejected_reason}）；原始任务包已保留，"
                    "请检查商品图绑定或 CUT 接单规则"
                    if permanently_rejected and rejected_reason
                    else "CUT 拒绝当前任务包（HTTP 409）；原始素材未被删除，请检查 CUT 接单日志"
                    if permanently_rejected
                    else "已送入剪辑，CUT 尚未回写接单状态；系统会继续自动检查，无需重复送剪"
                )
                await _write_claimed(
                    database,
                    task,
                    token,
                    {
                        "status": "READY",
                        "manifest_json": waiting_manifest,
                        "output_json": list(task.output_json or []),
                        "error_code": waiting_code,
                        "error_message": (
                            waiting_message
                            if waiting_code else None
                        ),
                        "failure_count": 0,
                        "next_run_at": datetime.now(UTC) + timedelta(
                            seconds=(300 if permanently_rejected
                                     else min(settings.cutflow_nas_scan_seconds, 300)),
                        ),
                    },
                )
                return
        else:
            if manifest.get("_acceptedManifestSha256") != digest:
                posted = await asyncio.to_thread(
                    cut_multi_submit_request,
                    settings,
                    cut_multi_request(task, manifest),
                )
                accepted = normalize_multi_status(task, posted)
                accepted_manifest = {**manifest, "_acceptedManifestSha256": digest}
                async with database.session_factory() as session:
                    persisted = await session.execute(
                        update(MultiCutflowTask)
                        .where(
                            MultiCutflowTask.id == task.id,
                            MultiCutflowTask.user_id == task.user_id,
                            MultiCutflowTask.lease_token == token,
                        )
                        .values(
                            status=accepted["status"],
                            manifest_json=accepted_manifest,
                            error_code=None,
                            error_message=None,
                            failure_count=0,
                            next_run_at=_next_retry(0),
                            lease_token=None,
                            lease_until=None,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    await session.commit()
                    if persisted.rowcount != 1:
                        return
                return
            response = await asyncio.to_thread(cut_multi_status_request, settings, task.id)
        result = normalize_multi_status(task, response)
        revision = check_status_revision(
            manifest,
            response,
            required=unified,
        )
        if revision.stale:
            stale_manifest = {**manifest, "_lastStaleRevision": revision.revision}
            await _write_claimed(
                database,
                task,
                token,
                {
                    "status": task.status,
                    "manifest_json": stale_manifest,
                    "output_json": list(task.output_json or []),
                    "error_code": "CUTFLOW_STALE_STATUS",
                    "error_message": "CUT 发布了比本地更旧的状态，已保留较新的状态",
                    "failure_count": 0,
                    "next_run_at": _next_retry(0),
                },
            )
            return
        manifest = remember_status_revision(manifest, revision)
        if result.get("cutflowBatchId"):
            manifest["_cutBatchId"] = result["cutflowBatchId"]
        remote_state = result["status"]
        values = {
            "status": remote_state,
            "manifest_json": manifest,
            "output_json": list(task.output_json or []),
            "error_code": None,
            "error_message": None,
            "failure_count": 0,
            "next_run_at": _next_retry(0),
        }
        if remote_state == "FAILED":
            error = response.get("error")
            remote_code = error.get("code") if isinstance(error, dict) else None
            code_usable = isinstance(remote_code, str) and bool(
                re.fullmatch(r"[A-Z0-9_]{1,80}", remote_code)
            )
            if not code_usable:
                remote_code = "CUTFLOW_FAILED"
            # Say out loud when the remote side gave us nothing to work with.
            # A bare "execution failed" cost a whole round trip last time.
            values.update(
                error_code=remote_code,
                error_message=(
                    "CUT 多源任务执行失败，且未在状态中提供 error 详情（需 CUT 侧补充）"
                    if not code_usable
                    else "CUT 多源任务执行失败"
                ),
                next_run_at=None,
            )
        elif remote_state == "UNKNOWN":
            # Unknown is an observation, never an invitation to create another task.
            values.update(
                error_code="CUTFLOW_STATUS_UNKNOWN",
                error_message="CUT 暂未确认任务状态，系统将继续查询",
            )
        elif remote_state == "PENDING_REVIEW":
            if not result["outputs"]:
                raise CutflowHandoffError("CUTFLOW_OUTPUT_MISSING", "CUT 待审核回执没有成片")
            saved = await import_multi_outputs(task, result, settings, object_store)
            if not saved:
                raise CutflowHandoffError("CUTFLOW_OUTPUT_MISSING", "CUT 待审核回执没有可导入成片")
            values.update(status="PENDING_REVIEW", output_json=saved)
    except Exception as exc:
        failures = (task.failure_count or 0) + 1
        error_code = (
            exc.code if isinstance(exc, CutflowHandoffError) else "CUTFLOW_RETRY"
        )
        if error_code == "CUTFLOW_CONTRACT_UPGRADE_REQUIRED":
            # A task retains its manifest and wire contract for life. Re-sending
            # a legacy manifest under the current contract creates a mixed
            # descriptor that CUT must reject, so stop the retry loop and make
            # the required user action explicit.
            values = {
                "status": "FAILED",
                "error_code": error_code,
                "error_message": str(exc),
                "failure_count": failures,
                "next_run_at": None,
            }
        else:
            # Keep recoverable transport, media and delivery failures pollable
            # under the same task identity.
            values = {
                "status": task.status,
                "error_code": error_code,
                "error_message": str(exc)
                if isinstance(exc, CutflowHandoffError)
                else "CUT 多源状态暂时不可读取，系统将使用同一任务编号继续查询",
                "failure_count": failures,
                "next_run_at": _next_retry(failures),
            }
    async with database.session_factory() as session:
        written = await session.execute(
            update(MultiCutflowTask)
            .where(
                MultiCutflowTask.id == task.id,
                MultiCutflowTask.user_id == task.user_id,
                MultiCutflowTask.lease_token == token,
            )
            .values(**values, lease_token=None, lease_until=None)
            .execution_options(synchronize_session=False)
        )
        if written.rowcount == 1:
            await _reconcile_terminal_cutflow_usage(session, task, values)
        await session.commit()
        if written.rowcount != 1:
            return


async def tick_multi(database: Database, settings: Settings, object_store: ObjectStore) -> bool:
    """Claim one task for status-only polling; disabled until release evidence exists."""
    if not multi_worker_ready(settings):
        return False
    require_configuration(settings)
    now = datetime.now(UTC)
    token = str(uuid.uuid4())
    # A freshly created task has no manifest yet: publishing the package and
    # POSTing it is this worker's first job, so `manifest_path` must NOT be an
    # eligibility requirement. Gating on it made every new task unclaimable and
    # left the publish/submit branch below unreachable.
    eligible = (
        MultiCutflowTask.status.not_in(["FAILED", "CANCELED", "PENDING_REVIEW"]),
        or_(MultiCutflowTask.lease_until.is_(None), MultiCutflowTask.lease_until < now),
        or_(MultiCutflowTask.next_run_at.is_(None), MultiCutflowTask.next_run_at <= now),
    )
    async with database.session_factory() as session:
        task = await session.scalar(
            select(MultiCutflowTask)
            .where(*eligible)
            .order_by(MultiCutflowTask.next_run_at, MultiCutflowTask.created_at)
            .limit(1)
        )
        if task is None:
            return False
        claimed = await session.execute(
            update(MultiCutflowTask)
            .where(
                MultiCutflowTask.id == task.id,
                *eligible,
            )
            .values(lease_token=token, lease_until=now + timedelta(minutes=5))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if claimed.rowcount != 1:
            return False
        await session.refresh(task)
    heartbeat = asyncio.create_task(_heartbeat(database, task.id, token))
    try:
        await process_multi_task(database, settings, object_store, task, token)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    return True
