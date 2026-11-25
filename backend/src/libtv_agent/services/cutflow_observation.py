"""Local delivery telemetry; never modifies an immutable NAS package or starts work."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from libtv_agent.config import Settings
from libtv_agent.services.cutflow_status_reader import ERROR_CODES


def timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def path_for(settings: Settings, handoff_id: uuid.UUID) -> Path:
    return settings.artifact_root / "cutflow-observations" / f"{uuid.UUID(str(handoff_id))}.json"


def load(settings: Settings, handoff_id: uuid.UUID) -> dict:
    try:
        path = path_for(settings, handoff_id)
        if path.stat().st_size > 16_384:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(settings: Settings, handoff_id: uuid.UUID, value: dict) -> None:
    """Called only after a fenced DB update succeeds. Telemetry is not business state."""
    _save(path_for(settings, handoff_id), value)


def _save(target: Path, value: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def nas_probe(settings: Settings, timeout: float = 10) -> str:
    """Bound SMB hangs in a disposable process; no network IO on the API loop."""
    if settings.cutflow_transport != "lan" or settings.cutflow_handoff_root is None:
        return "NOT_CONFIGURED"
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("cutflow_status_reader.py")),
             str(settings.cutflow_handoff_root), "--probe"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return "NAS_PROBE_TIMEOUT"
    except OSError:
        return "NAS_UNAVAILABLE"
    return "OK" if result.returncode == 0 else ERROR_CODES.get(result.returncode, "NAS_UNAVAILABLE")


def nas_health(settings: Settings, now: datetime) -> dict:
    try:
        path = settings.artifact_root / "cutflow-observations" / "nas.json"
        value = json.loads(path.read_text(encoding="utf-8")) if path.stat().st_size < 4096 else {}
        checked = timestamp(value.get("checkedAt"))
        code = value.get("code")
        if code not in {"OK", "NOT_CONFIGURED", "NAS_PROBE_TIMEOUT", "NAS_UNAVAILABLE", *ERROR_CODES.values()}:
            raise ValueError("invalid health code")
        if checked and 0 <= (now - checked).total_seconds() <= 150:
            result = {"code": code, "checkedAt": checked.isoformat()}
            duration = value.get("probeDurationMs")
            if type(duration) is int and 0 <= duration <= 60000:
                result["probeDurationMs"] = duration
            return result
        return {"code": "STALE", "checkedAt": checked.isoformat() if checked else None}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"code": "UNKNOWN", "checkedAt": None}


def save_nas_health(settings: Settings, code: str, now: datetime, duration_ms: int | None = None) -> None:
    value = {"code": code, "checkedAt": now.isoformat()}
    if duration_ms is not None:
        value["probeDurationMs"] = duration_ms
    _save(settings.artifact_root / "cutflow-observations" / "nas.json", value)


def worker_pulse(settings: Settings, now: datetime) -> None:
    _save(settings.artifact_root / "cutflow-observations" / "worker.json",
          {"heartbeatAt": now.isoformat(), "version": "classic-cutflow/20261018"})


def worker_health(settings: Settings, now: datetime) -> dict:
    try:
        path = settings.artifact_root / "cutflow-observations" / "worker.json"
        value = json.loads(path.read_text(encoding="utf-8")) if path.stat().st_size < 4096 else {}
        beat = timestamp(value.get("heartbeatAt")) if isinstance(value, dict) else None
    except (OSError, ValueError):
        beat = None
    return {"state": "online" if beat and 0 <= (now - beat).total_seconds() <= 90
            else "stale" if beat else "unknown",
            "heartbeatAt": beat.isoformat() if beat else None}


def matching(settings: Settings, handoff) -> dict:
    value = load(settings, handoff.id)
    return value if (
        value.get("handoffId") == handoff.handoff_key
        and value.get("manifestSha256") == handoff.manifest_json.get("manifestSha256")
    ) else {}


def public_snapshot(settings: Settings, handoff, now: datetime) -> dict:
    """Owner-scoped local snapshot. No network requests or raw remote diagnostics."""
    value = matching(settings, handoff)
    contact, progress = timestamp(value.get("observedAt")), timestamp(value.get("lastProgressAt"))
    diagnostic = timestamp(value.get("diagnosticsAt"))
    stage = value.get("remoteStage") or value.get("remoteStatus")
    stages = {"importing", "uploading", "reference_queued", "analyzing_reference",
              "creating_proxies", "detecting_products", "regroup_queued", "reference_ready",
              "batch_queued", "editing", "review", "revision_queued", "revising",
              "cancel_requested", "canceled", "completed", "failed", "analyze", "quality",
              "semantic", "clip", "render", "review_ready"}
    fresh = bool(diagnostic and 0 <= (now - diagnostic).total_seconds() <= 90)
    return {
        "checkedAt": now.isoformat(), "handoffKey": handoff.handoff_key,
        "batchId": handoff.cutflow_batch_id, "localStatus": handoff.status,
        "sender": worker_health(settings, now),
        "lastContactAt": contact.isoformat() if contact else None,
        "lastProgressAt": progress.isoformat() if progress else None,
        "remoteStage": stage if isinstance(stage, str) and stage in stages else None,
        "diagnosticsState": "available" if fresh else "stale" if diagnostic else "unavailable",
        "workerAlive": value.get("workerAlive")
        if fresh and type(value.get("workerAlive")) is bool else None,
        "scopeMatches": value.get("scopeMatches")
        if fresh and type(value.get("scopeMatches")) is bool else None,
        "retryCount": handoff.failure_count,
        "nextRetryAt": handoff.next_run_at if handoff.status not in {
            "FAILED", "CANCELED", "PENDING_REVIEW",
        } else None,
        "errorCode": handoff.error_code,
    }


def observe(
    settings: Settings, previous: dict, response: dict, now: datetime,
) -> tuple[dict, tuple[str, str] | None]:
    """A successful poll is contact, not progress. Fresh remote diagnostics are optional."""
    handoff = response["handoffId"]
    digest = response["manifestSha256"]
    if previous.get("handoffId") != handoff or previous.get("manifestSha256") != digest:
        previous = {}
    stage = response.get("status")
    batch = response.get("batchId")
    diagnostic = response.get("diagnostics")
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    fresh = (
        diagnostic.get("schemaVersion") == "cutflow-diagnostics/1"
        and diagnostic.get("handoffId") == handoff
        and diagnostic.get("manifestSha256") == digest
        and (seen := timestamp(diagnostic.get("observedAt"))) is not None
        and 0 <= (now - seen).total_seconds() <= 90
    )
    revision = diagnostic.get("progressRevision") if fresh else None
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", revision):
        revision = None
    changed = (
        previous.get("remoteStatus") != stage
        or previous.get("batchId") != batch
        or (revision is not None and previous.get("progressRevision") != revision)
    )
    progressed = timestamp(previous.get("lastProgressAt"))
    if changed or progressed is None or progressed > now:
        progressed = now
    age = (now - progressed).total_seconds()
    value = {
        "handoffId": handoff, "manifestSha256": digest, "remoteStatus": stage,
        "batchId": batch, "observedAt": now.isoformat(),
        "lastProgressAt": progressed.isoformat(),
        "progressRevision": revision if revision is not None else previous.get("progressRevision"),
        "diagnosticsAvailable": bool(fresh),
    }
    if fresh:
        scope = diagnostic.get("workerHandoffIds")
        value.update(
            diagnosticsAt=seen.isoformat(),
            workerAlive=diagnostic.get("workerAlive")
            if type(diagnostic.get("workerAlive")) is bool else None,
            scopeMatches=handoff in scope if (
                isinstance(scope, list) and len(scope) <= 32
                and all(isinstance(item, str) for item in scope)
            ) else None,
        )
        for source, target in (
            ("stage", "remoteStage"), ("blockerCode", "remoteBlockerCode"),
            ("errorCode", "remoteErrorCode"),
        ):
            item = diagnostic.get(source)
            value[target] = item if (
                isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", item)
            ) else None
    warning = None
    active = stage not in {"failed", "completed", "canceled", "review"}
    if active and fresh:
        scope = diagnostic.get("workerHandoffIds")
        if diagnostic.get("workerAlive") is False:
            warning = ("CUTFLOW_WORKER_OFFLINE", "剪辑端报告处理进程离线，等待其恢复")
        elif (diagnostic.get("workerAlive") is True and isinstance(scope, list)
              and len(scope) <= 32 and all(isinstance(item, str) for item in scope)
              and handoff not in scope):
            warning = ("CUTFLOW_SCOPE_MISMATCH", "剪辑端处理范围未包含当前任务，请检查接单配置")
        if warning is None and value.get("remoteBlockerCode") == "WAITING_BUDGET_AUTHORIZATION":
            warning = ("CUTFLOW_BUDGET_REQUIRED", "剪辑端等待费用授权，当前任务已保留")
    if warning is None and stage in {"importing", "uploading"}:
        if age >= settings.cutflow_import_stall_seconds:
            warning = ("CUTFLOW_IMPORT_STALLED", "剪辑端已接收，但导入长时间无进展，正在继续查询")
    elif warning is None and active and age >= settings.cutflow_progress_stall_seconds:
        warning = ("CUTFLOW_PROGRESS_STALLED", "剪辑端长时间未报告阶段进展，正在继续查询")
    value["warningCode"] = warning[0] if warning else None
    return value, warning
