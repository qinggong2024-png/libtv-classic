"""Run with python -m libtv_agent.runner.cutflow_worker [--once].

Run one instance per LibTV installation. Row leases recover crashes and fence stale writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from libtv_agent.adapters.object_store import ObjectStore, create_object_store
from libtv_agent.config import Settings, get_settings
from libtv_agent.db import Database
from libtv_agent.domain.models import Asset, CutflowHandoff, DownloadArtifact
from libtv_agent.services import cutflow_observation
from libtv_agent.services.cut_coord_mailbox import (
    answer_reverse_receipt_probes,
)
from libtv_agent.services.cut_coord_mailbox import (
    consume_once as consume_coord_receipts,
)
from libtv_agent.services.cutflow_auto_submit import enqueue_completed
from libtv_agent.services.cutflow_auto_submit_v2 import enqueue_completed_v2
from libtv_agent.services.cutflow_handoff import (
    CutflowHandoffError,
    handoff_identity,
    import_outputs,
    lan_receipt_accepted,
    publish_package,
    read_nas_status,
    require_configuration,
)
from libtv_agent.services.cutflow_multi_worker import tick_multi
from libtv_agent.services.cutflow_policy import (
    LAN_CONTRACT,
    POLICY_VERSION,
    acknowledged_policy,
    editing_failure,
    saved_policy,
)
from libtv_agent.services.cutflow_receipts import ensure_receipts
from sqlalchemy import or_, select, update


def storage_failure_code(error: BaseException) -> str:
    """Safe operator diagnostics without logging NAS paths or credentials."""
    winerror = getattr(error, "winerror", None)
    if winerror == 1326:
        return "NAS_AUTH_FAILED"
    if winerror in {5, 65}:
        return "NAS_ACCESS_DENIED"
    if winerror in {53, 67, 1231}:
        return "NAS_UNREACHABLE"
    return type(error).__name__


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ControlHTTPError(CutflowHandoffError):
    def __init__(self, status_code: int):
        super().__init__("CUTFLOW_HTTP_ERROR", f"Cutflow 控制接口返回 HTTP {status_code}")
        self.status_code = status_code


def control_request(settings: Settings, method: str, suffix: str, payload=None) -> dict:
    require_configuration(settings)
    request = Request(
        (settings.cutflow_base_url or "").rstrip("/")
        + "/api/integrations/libtv" + suffix,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            **({"Authorization": f"Bearer {settings.cutflow_service_token}"}
               if settings.cutflow_service_token else {}),
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with build_opener(NoRedirect()).open(
            request,
            timeout=settings.cutflow_request_timeout_seconds,
        ) as response:
            raw = response.read(1_048_577)
    except HTTPError as exc:
        # Do not log remote response bodies, credentials, or filesystem paths.
        raise ControlHTTPError(exc.code) from None
    if len(raw) > 1_048_576:
        raise CutflowHandoffError("INVALID_RESULT", "Cutflow 控制响应过大")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise CutflowHandoffError("INVALID_RESULT", "Cutflow 控制响应格式错误")
    return result


def recoverable_control_failure(exc: Exception) -> bool:
    return (
        isinstance(exc, (URLError, TimeoutError, ConnectionError))
        or isinstance(exc, ControlHTTPError)
        and (exc.status_code in {404, 408, 429} or exc.status_code >= 500)
        or isinstance(exc, CutflowHandoffError) and exc.code == "CUTFLOW_RECEIPT_MISSING"
    )


async def _heartbeat(database: Database, handoff_id: uuid.UUID, token: str) -> None:
    while True:
        await asyncio.sleep(20)
        async with database.session_factory() as session:
            await session.execute(
                update(CutflowHandoff)
                .where(CutflowHandoff.id == handoff_id, CutflowHandoff.lease_token == token)
                .values(lease_until=datetime.now(UTC) + timedelta(minutes=5))
            )
            await session.commit()


async def tick(database: Database, settings: Settings, object_store: ObjectStore) -> bool:
    require_configuration(settings)
    now = datetime.now(UTC)
    token = str(uuid.uuid4())
    eligible = (
        CutflowHandoff.status.not_in(["FAILED", "CANCELED"]),
        or_(CutflowHandoff.contract_version != LAN_CONTRACT,
            CutflowHandoff.status != "PENDING_REVIEW"),
        (CutflowHandoff.contract_version == LAN_CONTRACT
         if settings.cutflow_transport == "lan"
         else CutflowHandoff.contract_version != LAN_CONTRACT),
        or_(CutflowHandoff.lease_until.is_(None), CutflowHandoff.lease_until < now),
        or_(CutflowHandoff.next_run_at.is_(None), CutflowHandoff.next_run_at <= now),
    )
    async with database.session_factory() as session:
        row = await session.scalar(
            select(CutflowHandoff)
            .where(*eligible)
            .order_by(CutflowHandoff.next_run_at, CutflowHandoff.created_at)
            .limit(1)
        )
        if row is None:
            return False
        claimed = await session.execute(
            update(CutflowHandoff)
            .where(CutflowHandoff.id == row.id, *eligible)
            .values(lease_token=token, lease_until=now + timedelta(minutes=5))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if claimed.rowcount != 1:
            return False
        await session.refresh(row)
        artifact = await session.get(DownloadArtifact, row.artifact_id)
        references = list(await session.scalars(select(Asset).where(
            Asset.parent_task_id == row.parent_task_id,
            Asset.role == "PRODUCT", Asset.deleted_at.is_(None),
        )))
    heartbeat = asyncio.create_task(_heartbeat(database, row.id, token))
    terminal = False
    observation = None
    hybrid = False
    try:
        lan = row.contract_version == LAN_CONTRACT
        identity = handoff_identity(row)
        policy = saved_policy(row)
        guided_lan = lan and policy["version"] == POLICY_VERSION
        hybrid = guided_lan and settings.cutflow_nas_status_enabled
        if not row.manifest_path:
            descriptor = await publish_package(
                row, artifact, settings, object_store, product_references=references,
            )
            # Persist the published descriptor before contacting Cutflow. A lost
            # HTTP response can safely replay the exact same immutable import.
            async with database.session_factory() as session:
                persisted = await session.execute(
                    update(CutflowHandoff)
                    .where(CutflowHandoff.id == row.id, CutflowHandoff.lease_token == token)
                    .values(
                        manifest_path=f"inbox/{row.handoff_key}/manifest.json",
                        manifest_json=descriptor,
                        status="READY",
                    )
                )
                await session.commit()
                if persisted.rowcount != 1:
                    return True
            row.manifest_json = descriptor
            row.manifest_path = f"inbox/{row.handoff_key}/manifest.json"
            row.status = "READY"
        # A scan may have accepted the package while the POST response was lost.
        # Before resubmission, consume that receipt using the same validators below.
        response = None
        if hybrid and not lan_receipt_accepted(row) and not row.cutflow_batch_id:
            response = await asyncio.to_thread(read_nas_status, row, settings)
        if response is None:
            try:
                if (not row.cutflow_batch_id and not lan_receipt_accepted(row)
                        and (not lan or row.status in {"QUEUED", "READY"})):
                    accepted = await asyncio.to_thread(
                        control_request, settings, "POST", "", row.manifest_json,
                    )
                    if lan and (
                        accepted.get("handoffId") != row.handoff_key
                        or accepted.get("manifestSha256") != row.manifest_json["manifestSha256"]
                    ):
                        raise CutflowHandoffError(
                            "INVALID_RESULT", "剪辑接单回执与源文件清单不匹配",
                        )
                    if lan:
                        # Persist successful acceptance before GET, including the crash
                        # window between POST acceptance and the first status response.
                        descriptor = {
                            **row.manifest_json,
                            "_acceptedManifestSha256": row.manifest_json["manifestSha256"],
                        }
                        async with database.session_factory() as session:
                            persisted = await session.execute(update(CutflowHandoff).where(
                                CutflowHandoff.id == row.id, CutflowHandoff.lease_token == token,
                            ).values(status="IMPORTING", manifest_json=descriptor))
                            await session.commit()
                            if persisted.rowcount != 1:
                                return True
                        row.status = "IMPORTING"
                        row.manifest_json = descriptor
                        observation, _warning = cutflow_observation.observe(
                            settings, {}, {**accepted, "status": "importing", "batchId": None},
                            datetime.now(UTC),
                        )
                    if guided_lan and not acknowledged_policy(
                        accepted, policy, row.manifest_json["manifestSha256"],
                    ):
                        raise CutflowHandoffError(
                            "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED",
                            "剪辑端尚未接纳 guided_auto 策略，请检查服务版本",
                        )
                suffix = f"/{row.handoff_key}" + ("" if lan else f"?{urlencode(identity)}")
                try:
                    response = await asyncio.to_thread(control_request, settings, "GET", suffix)
                except ControlHTTPError as exc:
                    # An accepted receipt disappearing is not permission to start another edit.
                    if lan and row.status not in {"QUEUED", "READY"} and exc.status_code == 404:
                        raise CutflowHandoffError(
                            "CUTFLOW_RECEIPT_MISSING",
                            "剪辑端暂时找不到已接收任务，保留原交付并继续查询",
                        ) from None
                    raise
            except Exception as exc:
                # Auth/protocol/policy failures remain visible; never mask them with a file.
                if not hybrid or not recoverable_control_failure(exc):
                    raise
                response = await asyncio.to_thread(read_nas_status, row, settings)
                if response is None:
                    raise
        if response.get("handoffId") != row.handoff_key:
            raise CutflowHandoffError("INVALID_RESULT", "Cutflow 交接标识不匹配")
        if lan and response.get("manifestSha256") != row.manifest_json["manifestSha256"]:
            raise CutflowHandoffError("INVALID_RESULT", "剪辑状态与源文件清单不匹配")
        if policy and (not lan or guided_lan) and not acknowledged_policy(
            response, policy, row.manifest_json["manifestSha256"]
        ):
            raise CutflowHandoffError(
                "CUTFLOW_AUTO_EDIT_NOT_ACCEPTED", "剪辑端尚未接纳当前自动剪辑策略，请检查服务版本"
            )
        remote_status = response.get("status")
        if not isinstance(remote_status, str) or remote_status not in {
            "importing",
            "uploading",
            "reference_queued",
            "analyzing_reference",
            "creating_proxies",
            "detecting_products",
            "regroup_queued",
            "reference_ready",
            "batch_queued",
            "editing",
            "review",
            "revision_queued",
            "revising",
            "cancel_requested",
            "canceled",
            "completed",
            "failed",
        }:
            raise CutflowHandoffError("INVALID_RESULT", "Cutflow 状态未识别")
        batch_id = response.get("batchId")
        if batch_id is not None:
            uuid.UUID(batch_id)
        if row.cutflow_batch_id and batch_id != row.cutflow_batch_id:
            raise CutflowHandoffError("INVALID_RESULT", "Cutflow 批次标识发生变化")
        if lan:
            previous = await asyncio.to_thread(cutflow_observation.load, settings, row.id)
            observation, warning = cutflow_observation.observe(
                settings, previous, response, datetime.now(UTC),
            )
            # Keep accepted identity even if NAS is temporarily unreachable or
            # the remote job failed. Recovery must query this receipt only.
            row.status = "IMPORTING" if remote_status in {"importing", "uploading"} else "EDITING"
            row.cutflow_batch_id = batch_id
            row.manifest_json = {**row.manifest_json,
                                 "_acceptedManifestSha256": row.manifest_json["manifestSha256"]}
        if remote_status == "failed":
            if policy:
                # CUT preserves execution completion while projecting temporary
                # delivery faults as failed/empty outputs. Keep polling the
                # same accepted receipt so restoring its files can recover it.
                error = response.get("error")
                delivery_messages = {
                    "NAS_UNAVAILABLE": "剪辑端成片暂时不可读取，保留原任务并自动重试回收",
                    "HASH_MISMATCH": "剪辑端成片哈希不匹配，等待原文件恢复后自动重试回收",
                    "SIZE_MISMATCH": "剪辑端成片大小不匹配，等待原文件恢复后自动重试回收",
                }
                delivery_code = error.get("code") if isinstance(error, dict) else None
                if (hybrid and isinstance(delivery_code, str)
                        and delivery_code in delivery_messages and response.get("outputs") == []):
                    raise CutflowHandoffError(
                        "CUTFLOW_MEDIA_UNAVAILABLE", delivery_messages[delivery_code],
                    )
                terminal = True
                code, message = editing_failure(response)
                raise CutflowHandoffError(code, message)
            raise CutflowHandoffError("CUTFLOW_IMPORT_FAILED", "Cutflow 处理失败，请检查剪辑端")
        outputs = response.get("outputs", [])
        if not isinstance(outputs, list):
            raise CutflowHandoffError("INVALID_RESULT", "Cutflow 成片列表无效")
        if lan and remote_status == "completed" and not outputs:
            raise CutflowHandoffError("INVALID_RESULT", "剪辑完成回执缺少成片")
        # CUT can publish a fully rendered version for human review. Importing
        # it still produces PENDING_REVIEW locally, never an approval.
        if lan and remote_status not in {"review", "completed"} and outputs:
            raise CutflowHandoffError("INVALID_RESULT", "成片尚未发布完成")
        try:
            saved = await import_outputs(row, outputs, settings, object_store)
        except OSError:
            if not lan:
                raise
            raise CutflowHandoffError(
                "CUTFLOW_MEDIA_UNAVAILABLE", "成片文件暂时不可读取，保留原任务并自动重试回收",
            ) from None
        state = (
            "CANCELED"
            if remote_status == "canceled"
            else "PENDING_REVIEW"
            if saved
            else (
                "AWAITING_EDIT"
                if remote_status in {"uploading", "reference_ready"}
                else "CANCELED"
                if remote_status == "canceled"
                else "EDITING"
                if batch_id or (lan and remote_status not in {"importing", "uploading"})
                else "IMPORTING"
            )
        )
        values = {
            "status": state,
            "manifest_json": row.manifest_json,
            "outputs_json": saved,
            "cutflow_batch_id": batch_id,
            "cutflow_editor_url": (
                settings.cutflow_base_url.rstrip("/") if batch_id and not lan else None
            ),
            "error_code": None,
            "error_message": None,
            "failure_count": 0,
            "next_run_at": datetime.now(UTC) + timedelta(seconds=min(
                settings.cutflow_poll_seconds,
                settings.cutflow_nas_scan_seconds if hybrid else settings.cutflow_poll_seconds,
            )),
        }
        if lan and warning:
            values.update(error_code=warning[0], error_message=warning[1])
    except Exception as exc:
        failures = row.failure_count + 1
        # Once accepted, a network outage must not abandon a job or POST it again.
        transient = (
            row.contract_version == LAN_CONTRACT
            and (row.status not in {"QUEUED", "READY"} or hybrid and bool(row.manifest_path))
            and (
                isinstance(exc, (URLError, TimeoutError, ConnectionError))
                or isinstance(exc, ControlHTTPError)
                and (exc.status_code in {408, 429} or exc.status_code >= 500)
                or isinstance(exc, CutflowHandoffError)
                and exc.code in {"CUTFLOW_RECEIPT_MISSING", "CUTFLOW_MEDIA_UNAVAILABLE"}
                or hybrid and isinstance(exc, ControlHTTPError) and exc.status_code == 404
            )
        )
        values = {
            "failure_count": failures,
            "manifest_json": row.manifest_json,
            "cutflow_batch_id": row.cutflow_batch_id,
            "status": "FAILED" if terminal or (failures >= 5 and not transient) else row.status,
            "error_code": exc.code if isinstance(exc, CutflowHandoffError) else "CUTFLOW_RETRY",
            "error_message": str(exc)
            if isinstance(exc, CutflowHandoffError)
            else "交接暂时失败，系统将重试；请检查网络、NAS 和剪辑端",
            "next_run_at": None if terminal or (failures >= 5 and not transient)
            else datetime.now(UTC) + timedelta(
                seconds=min(settings.cutflow_nas_scan_seconds if hybrid and transient else 300,
                            15 * 2**min(failures, 5))
            ),
        }
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    async with database.session_factory() as session:
        written = await session.execute(
            update(CutflowHandoff)
            .where(CutflowHandoff.id == row.id, CutflowHandoff.lease_token == token)
            .values(**values, lease_token=None, lease_until=None)
        )
        await session.commit()
        if written.rowcount == 1 and observation is not None:
            # A full/read-only telemetry disk must not turn a received output into failure.
            with suppress(OSError):
                await asyncio.to_thread(cutflow_observation.save, settings, row.id, observation)
    return True


async def _worker_pulse(settings: Settings) -> None:
    while True:
        with suppress(OSError):
            await asyncio.to_thread(cutflow_observation.worker_pulse, settings, datetime.now(UTC))
        await asyncio.sleep(15)


async def run(once: bool, stopping=None) -> None:
    settings = get_settings()
    require_configuration(settings)
    database = Database(settings.database_url)
    pulse = asyncio.create_task(_worker_pulse(settings)) if not once else None
    clean_stop = False
    try:
        store = create_object_store(settings)
        last_receipt_sweep = 0.0
        last_coord_sweep = 0.0
        last_auto_sweep = 0.0
        while stopping is None or not stopping.is_set():
            failed = False
            for operation_name, operation in (
                ("enqueue_completed", enqueue_completed_v2
                 if settings.cutflow_multi_source_enabled and settings.cutflow_transport == "lan"
                 else enqueue_completed),
                ("single_source_poll", tick),
                ("multi_source_poll", tick_multi),
            ):
                if stopping is not None and stopping.is_set():
                    break
                if operation_name == "enqueue_completed":
                    # Automatic admission is a separate, low-frequency sweep;
                    # never starve normal CUT status polling.
                    if not once and monotonic() - last_auto_sweep < 30:
                        continue
                    last_auto_sweep = monotonic()
                try:
                    await operation(database, settings, store)
                except Exception as exc:
                    if once:
                        raise
                    # Discovery failure must not starve existing accepted jobs.
                    # Never dump raw exceptions, paths or credentials.
                    error_code = (
                        exc.code
                        if isinstance(exc, CutflowHandoffError)
                        else type(exc).__name__
                    )
                    logging.getLogger(__name__).error(
                        "CUTFLOW_WORKER_CYCLE_FAILED operation=%s error_code=%s; retry in 15s",
                        operation_name,
                        error_code,
                    )
                    failed = True
            if stopping is not None and stopping.is_set():
                break
            if once or monotonic() - last_receipt_sweep >= 30:
                try:
                    await ensure_receipts(database, settings)
                except Exception as exc:
                    logging.getLogger(__name__).error(
                        "CUTFLOW_RECEIPT_SWEEP_FAILED error_code=%s; retry in 30s",
                        storage_failure_code(exc),
                    )
                    failed = True
                last_receipt_sweep = monotonic()
            # COORD is an operational mailbox, separate from the CUT business
            # outbox. Confirm only that matched ack bytes were read; never
            # execute instructions or mutate CUT tasks from a COORD receipt.
            if (settings.cutflow_transport == "lan"
                    and settings.cutflow_handoff_root is not None
                    and (once or monotonic() - last_coord_sweep >= 30)):
                try:
                    await asyncio.to_thread(consume_coord_receipts, settings.cutflow_handoff_root)
                    await asyncio.to_thread(
                        answer_reverse_receipt_probes, settings.cutflow_handoff_root
                    )
                except (OSError, ValueError) as exc:
                    logging.getLogger(__name__).warning(
                        "CUT_COORD_RECEIPT_READ_FAILED error_code=%s; retry in 30s",
                        storage_failure_code(exc),
                    )
                last_coord_sweep = monotonic()
            if once:
                break
            if stopping is None:
                await asyncio.sleep(15 if failed else 1)
            else:
                await asyncio.to_thread(stopping.wait, 15 if failed else 1)
        clean_stop = True
    finally:
        if pulse is not None:
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse
        await database.dispose()
        if stopping is not None and clean_stop and stopping.is_set():
            stopping.publish("DRAINED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(run(parser.parse_args().once))
