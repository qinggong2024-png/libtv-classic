"""Bounded upload diagnostics: never log file contents, headers or field inputs."""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import Request

logger = logging.getLogger(__name__)


def upload_diagnostic_id(request: Request) -> str:
    diagnostic_id = getattr(request.state, "upload_diagnostic_id", None)
    if diagnostic_id is None:
        diagnostic_id = uuid.uuid4().hex
        request.state.upload_diagnostic_id = diagnostic_id
    return diagnostic_id


def log_upload_failure(request: Request, payload: dict[str, object]) -> None:
    if request.url.path != "/api/v1/uploads":
        return
    record: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "correlationId": payload["correlationId"],
        "code": payload["code"],
        "stage": getattr(request.state, "upload_stage", "request_validation"),
    }
    size = getattr(request.state, "upload_size_bytes", None)
    if isinstance(size, int):
        record["sizeBytes"] = size
    details = payload.get("details")
    if isinstance(details, dict):
        for key in ("width", "height", "minDimension", "maxDimension", "maxBytes"):
            value = details.get(key)
            if type(value) is int:
                record[key] = value
        for key in ("actualFormat", "exceptionType"):
            value = details.get(key)
            if isinstance(value, str) and value.isascii() and value.isalnum():
                record[key] = value[:40]
    field_errors = payload.get("fieldErrors")
    if isinstance(field_errors, list):
        allowed = {"body", "header", "file", "Idempotency-Key", "X-Source-Image-Output-Id"}
        record["fields"] = [
            [part for part in error.get("loc", []) if isinstance(part, str) and part in allowed]
            for error in field_errors[:8] if isinstance(error, dict)
        ]
    logger.warning("upload.failure %s", json.dumps(record, sort_keys=True))
