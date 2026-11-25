"""Minimal, credential-free Kling receipts independent of the business database."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KlingSubmissionReceipt:
    root: Path
    operation_key: str
    provider: str
    account_id: str
    fingerprint: str

    @property
    def path(self) -> Path:
        name = hashlib.sha256(self.operation_key.encode()).hexdigest()
        return self.root / "kling" / "submission-receipts" / f"{name}.json"

    def _identity(self) -> dict[str, Any]:
        return {
            "version": 1, "operationKey": self.operation_key, "provider": self.provider,
            "accountId": self.account_id, "fingerprint": self.fingerprint,
        }

    def load(self) -> dict[str, Any] | None:
        try:
            with self.path.open(encoding="utf-8") as handle:
                text = handle.read(16_385)
        except FileNotFoundError:
            return None
        if len(text) > 16_384:
            raise ValueError("Oversized Kling submission receipt")
        value = json.loads(text)
        if not isinstance(value, dict) or any(
            value.get(key) != expected for key, expected in self._identity().items()
        ):
            raise ValueError("Kling receipt identity does not match this operation")
        if value.get("status") not in {
            "STARTED", "ACCEPTED", "UNCERTAIN", "REJECTED", "FAILED_BEFORE_SUBMIT",
        }:
            raise ValueError("Invalid Kling receipt status")
        task_id = value.get("providerTaskId")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("Invalid Kling receipt task ID")
        if task_id and value["status"] not in {"ACCEPTED", "UNCERTAIN"}:
            raise ValueError("Conflicting Kling receipt acceptance evidence")
        if not isinstance(value.get("startedAt"), str):
            raise ValueError("Missing Kling receipt boundary timestamp")
        if value["status"] == "ACCEPTED" and not task_id:
            raise ValueError("Accepted Kling receipt has no task ID")
        return value

    @staticmethod
    def _write(handle, value: dict[str, Any]) -> None:
        json.dump(value, handle, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())

    def begin(self) -> dict[str, Any] | None:
        """Exclusive marker before paid invocation; only verified 429 may retry."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {**self._identity(), "status": "STARTED",
                 "startedAt": datetime.now(UTC).isoformat()}
        try:
            with self.path.open("x", encoding="utf-8") as handle:
                self._write(handle, value)
        except FileExistsError:
            previous = self.load()
            if previous["status"] != "REJECTED" or previous.get("providerTaskId"):
                return previous
            self.finish("STARTED")
        return None

    def finish(
        self, status: str, *, task_id: str | None = None, model: str | None = None,
        credits: int | None = None, error_code: str | None = None,
    ) -> None:
        previous = self.load()
        if previous is None:
            raise ValueError("Missing pre-invocation Kling receipt")
        old_id = previous.get("providerTaskId")
        if old_id and (old_id != task_id or status not in {"ACCEPTED", "UNCERTAIN"}):
            raise ValueError("Cannot overwrite existing Kling acceptance evidence")
        value = {
            **self._identity(), "status": status, "startedAt": previous["startedAt"],
            "finishedAt": datetime.now(UTC).isoformat(), "providerTaskId": task_id,
            "model": model, "creditsConsumed": credits, "errorCode": error_code,
        }
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                self._write(handle, value)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
