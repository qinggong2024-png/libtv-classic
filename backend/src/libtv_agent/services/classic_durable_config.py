"""Shared, non-secret Classic durable execution configuration.

Loaded once by the launcher and checked again at acceptance boundaries.
A file change requires a controlled rollout; it is not a live feature toggle.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

NAME = "durable-execution.json"
READY = "durable-runner-ready.json"
DEFAULT = {
    "version": 1,
    "analysis": False,
    "confirmation": False,
    "analysisConcurrency": 4,
    "runnerSourceRevision": None,
}


def read_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT)
    if (
        not isinstance(data, dict)
        or set(data) != set(DEFAULT)
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["analysis"]) is not bool
        or type(data["confirmation"]) is not bool
        or type(data["analysisConcurrency"]) is not int
        or not 1 <= data["analysisConcurrency"] <= 32
    ):
        raise ValueError("invalid Classic durable execution configuration")
    revision = data["runnerSourceRevision"]
    if data["analysis"] or data["confirmation"]:
        if (
            not isinstance(revision, str)
            or len(revision) != 64
            or any(c not in "0123456789abcdef" for c in revision)
        ):
            raise ValueError("enabled durable execution requires a pinned Runner source")
    elif revision is not None:
        raise ValueError("disabled configuration must not carry a Runner pin")
    return data


def revision(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def launcher_environment(path: Path) -> dict[str, str]:
    data = read_config(path)
    return {
        "CLASSIC_DURABLE_CONFIG_FILE": str(path),
        "CLASSIC_DURABLE_CONFIG_REVISION": revision(data),
        "CLASSIC_DURABLE_ANALYSIS_ENABLED": str(data["analysis"]).lower(),
        "CLASSIC_DURABLE_CONFIRMATION_ENABLED": str(data["confirmation"]).lower(),
        "CLASSIC_DURABLE_ANALYSIS_CONCURRENCY": str(data["analysisConcurrency"]),
    }


def matches_loaded(settings, data: dict) -> bool:
    return (
        settings.classic_durable_config_revision == revision(data)
        and settings.classic_durable_analysis_enabled == data["analysis"]
        and settings.classic_durable_confirmation_enabled == data["confirmation"]
        and settings.classic_durable_analysis_concurrency == data["analysisConcurrency"]
    )


HANDOFF = "runner-online-handoff.json"
HANDOFF_QUEUE_STATUS = "QUEUING_DURING_HANDOFF"
MAX_HANDOFF_SECONDS = 180


def effective_runner_revision(runtime: Path, data: dict) -> str | None:
    """V1 config remains immutable; the validated launcher pin selects code.

    Missing pins retain the initial V1 contract. A malformed present pin
    must fail closed. The supervisor verifies the actual source hash.
    """
    try:
        pin = json.loads((runtime / "worker-runner-pin.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return data["runnerSourceRevision"]
    if (not isinstance(pin, dict) or set(pin) != {"version", "releaseRoot", "sourceRevision"}
            or type(pin["version"]) is not int or pin["version"] != 1
            or not isinstance(pin["releaseRoot"], str) or not Path(pin["releaseRoot"]).is_absolute()
            or not _is_revision(pin["sourceRevision"])):
        raise ValueError("invalid Classic Runner source pin")
    return pin["sourceRevision"]


def _is_revision(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def handoff_queue_ready(runtime: Path, data: dict) -> bool:
    """Authorize durable enqueue only, not actual provider execution.

    The operator proof is short-lived and tied to one exact old checkpoint
    and the approved new pin. An unrelated failure remains unavailable.
    """
    try:
        marker = json.loads((runtime / HANDOFF).read_text(encoding="utf-8"))
        checkpoint = json.loads(
            (runtime / "checkpoint-runner-status.json").read_text(encoding="utf-8")
        )
        request = json.loads(
            (runtime / "checkpoint-runner-request.json").read_text(encoding="utf-8")
        )
        now = time.time()
        if (not isinstance(marker, dict)
                or set(marker) != {"version", "configRevision", "fromRevision", "toRevision",
                                   "checkpoint", "createdAt", "expiresAt"}
                or type(marker["version"]) is not int or marker["version"] != 1
                or marker["configRevision"] != revision(data)
                or not _is_revision(marker["fromRevision"])
                or not _is_revision(marker["toRevision"])
                or marker["fromRevision"] == marker["toRevision"]
                or not (marker["createdAt"] <= now <= marker["expiresAt"]
                        <= marker["createdAt"] + MAX_HANDOFF_SECONDS)
                or request != marker["checkpoint"]
                or request["kind"] != "runner"
                or request["sourceRevision"] != marker["fromRevision"]
                or checkpoint["kind"] != "runner"):
            return False
        pin = effective_runner_revision(runtime, data)
        old_identity = {k: v for k, v in checkpoint.items() if k != "state"}
        if old_identity == request:
            return (checkpoint["state"] in ("RUNNING", "DRAINING", "DRAINED")
                    and (pin == marker["fromRevision"]
                         or (pin == marker["toRevision"] and checkpoint["state"] == "DRAINED")))
        return (pin == marker["toRevision"]
                and checkpoint["sourceRevision"] == pin and checkpoint["state"] == "RUNNING")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def runner_ready(runtime: Path, data: dict) -> bool:
    import psutil

    try:
        receipt = json.loads((runtime / READY).read_text(encoding="utf-8"))
        checkpoint = json.loads(
            (runtime / "checkpoint-runner-status.json").read_text(encoding="utf-8")
        )
        now = time.time()
        if (
            receipt["version"] != 1
            or receipt["ready"] is not True
            or receipt["configRevision"] != revision(data)
            or receipt["sourceRevision"] != effective_runner_revision(runtime, data)
            or receipt["analysis"] is not data["analysis"]
            or receipt["confirmation"] is not data["confirmation"]
            or receipt["analysisConcurrency"] != data["analysisConcurrency"]
            or not 0 <= now - receipt["heartbeat"] <= 15
            or checkpoint["state"] != "RUNNING"
            or checkpoint["kind"] != "runner"
            or any(
                checkpoint[k] != receipt[k] for k in ("pid", "created", "token", "sourceRevision")
            )
        ):
            return False
        try:
            request = json.loads(
                (runtime / "checkpoint-runner-request.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            request = None
        if request == {k: v for k, v in checkpoint.items() if k != "state"}:
            return False
        process = psutil.Process(receipt["pid"])
        return process.is_running() and abs(process.create_time() - receipt["created"]) < 0.1
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return False


def acceptance_status(settings, feature: str) -> str:
    path = settings.classic_durable_config_file
    if path is None:
        # Isolated and non-Classic installations retain the existing settings contract.
        if settings.deployment_id == "libtv-classic-lan-0e3d1c8" and (
            settings.classic_durable_analysis_enabled
            or settings.classic_durable_confirmation_enabled
        ):
            return "CONFIG_MISSING"
        return "UNMANAGED"
    try:
        data = read_config(path)
        if not matches_loaded(settings, data):
            return "CONFIG_MISMATCH"
        if not data[feature]:
            return "DISABLED"
        if runner_ready(path.parent, data):
            return "READY"
        if handoff_queue_ready(path.parent, data):
            return HANDOFF_QUEUE_STATUS
        return "RUNNER_NOT_READY"
    except (OSError, ValueError, KeyError, TypeError):
        return "CONFIG_INVALID"


def require_acceptance(settings, feature: str, *, allow_handoff_queue: bool = False) -> None:
    from fastapi import HTTPException

    result = acceptance_status(settings, feature)
    if result == HANDOFF_QUEUE_STATUS and allow_handoff_queue:
        return
    if result not in ("UNMANAGED", "DISABLED", "READY"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DURABLE_EXECUTION_UNAVAILABLE",
                "reason": result,
                "message": "Durable execution configuration or Runner is not ready",
            },
        )
