from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from libtv_agent.adapters.kling_submission_receipts import KlingSubmissionReceipt
from libtv_agent.adapters.video_provider import (
    DownloadResult,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderOutput,
    ProviderSubmissionMatch,
    ProviderSubmissionResult,
    ProviderTaskResult,
    VideoGenerationRequest,
)
from libtv_agent.config import Settings

logger = logging.getLogger(__name__)

_CREDIT_BALANCE_TERMS = ("credit", "point", "灵感值", "积分", "余额")
_CREDIT_SHORTAGE_TERMS = ("insufficient", "not enough", "exhausted", "不足", "用完")


def is_credit_rejection(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in _CREDIT_BALANCE_TERMS) and any(
        term in lowered for term in _CREDIT_SHORTAGE_TERMS
    )


KLING_SKILL_NAME = "kling-cli"
KLING_SKILL_VERSION = "0.2.3"

_OOTD_PROFILES = frozenset((
    "allwe-ootd-sol-simple-v1", "allwe-ootd-sol-motion-v2.1", "allwe-ootd-sol-motion-v2.2",
))


@dataclass(frozen=True)
class CliProcessResult:
    exit_code: int
    stdout: str
    stderr: str


class KlingCliError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider_task_id: str | None = None,
        raw_status: str | None = None,
        details: dict[str, Any] | None = None,
        submitted: bool = False,
        uncertain: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider = "kling_official_cli"
        self.provider_task_id = provider_task_id
        self.raw_status = raw_status
        self.details = details or {}
        self.submitted = submitted
        self.uncertain = uncertain
        self.retry_after_seconds = retry_after_seconds


def parse_cli_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise KlingCliError(
            "KLING_CLI_OUTPUT_PARSE_FAILED", "Kling CLI did not return structured output"
        )
    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise KlingCliError("KLING_CLI_OUTPUT_PARSE_FAILED", "Kling CLI returned invalid JSON output")


def _confirmed_rate_limit_rejection(payload: dict[str, Any]) -> bool:
    """Only a structured HTTP 429 rejection can authorize another submit.

    Text-only limit messages and missing generationIds are not proof that
    the original paid request was rejected before acceptance.
    """
    if payload.get("ok") is not False or str(payload.get("status")) != "429":
        return False
    pending: list[Any] = [payload]
    inspected = 0
    while pending:
        inspected += 1
        if inspected > 1000:
            # Uninspectable envelopes cannot prove a paid request was rejected.
            return False
        candidate = pending.pop()
        if isinstance(candidate, dict):
            if any(
                candidate.get(key)
                for key in (
                    "generationId",
                    "generation_id",
                    "taskId",
                    "task_id",
                    "accepted",
                    "requestAccepted",
                )
            ):
                return False
            pending.extend(candidate.values())
        elif isinstance(candidate, list):
            pending.extend(candidate)
    return True


def _submission_evidence(*outputs: str) -> tuple[str | None, bool, bool, bool]:
    """Inspect both streams, not just their final JSON object.

    Return a unique handle, verified rejection, ambiguity and acceptance evidence.
    Earlier acceptance must override a later 429; conflicting handles must never
    be resolved by arbitrarily choosing the last line or preferring stdout.
    """
    payloads = []
    for output in outputs:
        try:
            whole = json.loads(output)
        except (ValueError, RecursionError):
            whole = None
        if isinstance(whole, dict):
            payloads.append(whole)
            continue
        for line in output.splitlines():
            try:
                payload = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
            if len(payloads) > 1000:
                return None, False, True, True
    values: set[str] = set()
    accepted = False
    pending = list(payloads)
    for _ in range(1000):
        if not pending:
            break
        item = pending.pop()
        if isinstance(item, dict):
            accepted = accepted or any(
                item.get(key) for key in (
                    "generationId", "generation_id", "taskId", "task_id",
                    "accepted", "requestAccepted",
                )
            )
            for key in ("generationId", "generation_id"):
                if isinstance(item.get(key), str) and item[key].strip():
                    values.add(item[key])
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    ambiguous = bool(pending) or len(values) > 1
    handle = next(iter(values)) if len(values) == 1 and not ambiguous else None
    rejected = bool(payloads) and not ambiguous and all(
        _confirmed_rate_limit_rejection(payload) for payload in payloads
    )
    return handle, rejected, ambiguous, accepted or ambiguous


def unwrap_cli_body(payload: dict[str, Any], *, submitted: bool = False) -> dict[str, Any]:
    if payload.get("ok") is False:
        body = payload.get("body")
        message = (
            body.get("message")
            if isinstance(body, dict)
            else payload.get("message") or payload.get("error") or body
        )
        text = str(message or "Kling CLI request failed")
        lowered = text.lower()
        if any(marker in lowered for marker in ("unauthorized", "not logged", "login required")):
            code = "KLING_CLI_NOT_AUTHENTICATED"
        elif is_credit_rejection(lowered):
            code = "KLING_INSUFFICIENT_CREDITS"
        elif _confirmed_rate_limit_rejection(payload) or any(
            marker in lowered for marker in ("rate limit", "too many requests", "限流")
        ):
            code = "KLING_RATE_LIMITED"
        else:
            code = "KLING_CLI_PROCESS_FAILED"
        rejected = code == "KLING_RATE_LIMITED" and _confirmed_rate_limit_rejection(payload)
        uncertain = submitted and not rejected and code != "KLING_INSUFFICIENT_CREDITS"
        raise KlingCliError(
            code,
            "Kling CLI rejected request (HTTP 429)" if rejected else text,
            retryable=code in {"KLING_RATE_LIMITED", "KLING_CLI_PROCESS_FAILED"},
            details={"status": payload.get("status"), "verifiedRejected": rejected},
            submitted=uncertain,
            uncertain=uncertain,
        )
    body = payload.get("body", payload)
    if not isinstance(body, dict):
        raise KlingCliError("KLING_CLI_OUTPUT_PARSE_FAILED", "Kling CLI JSON body is not an object")
    return body


def map_kling_status(raw_status: object) -> str:
    normalized = str(raw_status or "").strip().upper()
    if normalized in {"QUEUING", "PENDING", "SUBMITTED"}:
        return "queued"
    if normalized in {"RUNNING", "PROCESSING"}:
        return "running"
    if normalized in {"COMPLETED", "SUCCESS", "SUCCEED", "SUCCEEDED"}:
        return "succeeded"
    if normalized == "PARTIAL_COMPLETED":
        return "partial"
    if normalized in {"FAILED", "FAILURE", "ERROR", "CANCELLED", "CANCELED"}:
        return "failed"
    return "unknown"


def _video_model_items(capabilities: ProviderCapabilities) -> list[dict[str, Any]]:
    available = capabilities.raw_capabilities.get("availableModels")
    if not isinstance(available, dict):
        return []
    results: list[dict[str, Any]] = []
    for tool in ("omni_ref_video", "image_to_video"):
        group = available.get(tool)
        models = group.get("models") if isinstance(group, dict) else group
        if isinstance(models, list):
            results.extend(item for item in models if isinstance(item, dict))
    return results


def _omni_models(capabilities: ProviderCapabilities) -> set[str]:
    available = capabilities.raw_capabilities.get("availableModels")
    group = available.get("omni_ref_video") if isinstance(available, dict) else None
    models = group.get("models") if isinstance(group, dict) else group
    return (
        {
            str(item["model"])
            for item in models
            if isinstance(item, dict) and isinstance(item.get("model"), str)
        }
        if isinstance(models, list)
        else set()
    )


def _argument_specs(capabilities: ProviderCapabilities, model: str) -> list[dict[str, Any]]:
    for item in _video_model_items(capabilities):
        if isinstance(item, dict) and item.get("model") == model:
            arguments = item.get("arguments")
            return (
                [value for value in arguments if isinstance(value, dict)]
                if isinstance(arguments, list)
                else []
            )
    return []


def _input_specs(capabilities: ProviderCapabilities, model: str) -> list[dict[str, Any]]:
    for item in _video_model_items(capabilities):
        if isinstance(item, dict) and item.get("model") == model:
            inputs = item.get("inputs")
            return (
                [value for value in inputs if isinstance(value, dict)]
                if isinstance(inputs, list)
                else []
            )
    return []


def _allowed_values(specs: list[dict[str, Any]], *names: str) -> tuple[str, ...]:
    normalized_names = {name.replace("_", "").lower() for name in names}
    for spec in specs:
        name = str(spec.get("name") or "").replace("_", "").lower()
        if name not in normalized_names:
            continue
        values = spec.get("allowedValues")
        if isinstance(values, list):
            return tuple(str(value) for value in values)
    return ()


def _model_input_profile(capabilities: ProviderCapabilities, model: str) -> tuple[int, bool, bool]:
    names = {str(item.get("name") or "").lower() for item in _input_specs(capabilities, model)}
    numbered_images = {name for name in names if name.startswith("image_") and name[6:].isdigit()}
    supports_repeated_images = bool(numbered_images)
    supports_tail = any("tail" in name or "end" in name for name in names)
    if supports_repeated_images:
        return len(numbered_images), supports_tail, True
    return (2 if supports_tail else 1), supports_tail, False


def cli_model_limits(
    capabilities: ProviderCapabilities, model: str | None,
) -> dict[str, Any] | None:
    """Expose only the selected CLI model's inputs and durations to the UI."""
    if not model or model not in capabilities.supported_models:
        return None
    image_limit, supports_tail, numbered_images = _model_input_profile(capabilities, model)
    durations = list(_allowed_values(_argument_specs(capabilities, model), "duration"))
    return {
        "imageInputLimit": image_limit,
        "supportsMultipleImages": numbered_images or supports_tail,
        "videoDurations": durations,
        "tailImageVideoDurations": durations if numbered_images or supports_tail else [],
        # The adapter does not serialize Element bindings for any CLI model.
        "supportsElementReferences": False,
        "supportsProductReferences": numbered_images,
    }


def _all_allowed_values(specs: list[dict[str, Any]], *names: str) -> tuple[str, ...]:
    normalized_names = {name.replace("_", "").lower() for name in names}
    values: list[str] = []
    for spec in specs:
        name = str(spec.get("name") or "").replace("_", "").lower()
        if name not in normalized_names:
            continue
        allowed = spec.get("allowedValues")
        if not isinstance(allowed, list):
            allowed = spec.get("allowed_values")
        if isinstance(allowed, list):
            for value in allowed:
                text = str(value)
                if text not in values:
                    values.append(text)
    return tuple(values)


def _role_bound_prompt(request: VideoGenerationRequest) -> str:
    if request.director_native_profile in _OOTD_PROFILES:
        # Keep the proven single-frame OOTD contract byte-for-byte unchanged.
        # Omni CLI supplies all references as numbered --image inputs rather
        # than typed storyboard/product parameters; bind roles only for multi-shot
        # storyboard/product inputs, in the exact provider argument order.
        if len(request.image_paths) == 1 and len(request.product_reference_paths) <= 1:
            return request.prompt
        frame_count = len(request.image_paths)
        product_count = len(request.product_reference_paths)
        roles = ["图片1=唯一视频起始首帧"]
        roles.extend(
            f"图片{index}=后续镜头参考"
            for index in range(2, frame_count + 1)
        )
        roles.extend(
            f"图片{frame_count + index}="
            + ("商品主参考图" if index == 1 else "商品细节补充参考图")
            for index in range(1, product_count + 1)
        )
        role_guard = (
            "【图片角色，按上传顺序】" + "；".join(roles)
            + "。首帧以图片1的原始画面起镜，其他分镜只辅助后续构图、动作及景别，"
            "不要求逐张定格或强制固定切镜。商品图片仅控制衣服外观、文字、"
            "花纹与材质，不替换人物及场景。"
        )
        return f"{role_guard}\n\n{request.prompt}"
    if not request.product_reference_paths:
        return request.prompt
    if (
        "are ordered storyboards controlling scene, action, composition" in request.prompt
        or "are ordered visual references controlling identity" in request.prompt
        or "is the source image controlling identity" in request.prompt
        or "【输入图片用途，必须严格遵守】" in request.prompt
    ):
        return request.prompt
    storyboard_count = len(request.image_paths)
    product_start = storyboard_count + 1
    product_end = storyboard_count + len(request.product_reference_paths)
    storyboard_label = f"图片1至图片{storyboard_count}" if storyboard_count > 1 else "图片1"
    product_label = (
        f"图片{product_start}至图片{product_end}"
        if product_start != product_end
        else f"图片{product_start}"
    )
    product_order = (
        f"图片{product_start}是主产品参考图，"
        f"图片{product_start + 1}至图片{product_end}是补充产品参考图；"
        if product_start != product_end
        else f"图片{product_start}是主产品参考图；"
    )
    role_guard = (
        "【输入图片用途，必须严格遵守】"
        f"{storyboard_label}是分镜图，只用于决定视频的场景、构图、人物姿势、动作、"
        "镜头运动和镜头顺序；"
        f"{product_order}{product_label}只用于参考商品外观、款式、印花、Logo与可见文字；"
        "不得采用产品参考图的人物、背景、构图、摆放方式或拍摄姿势，"
        "不得把白底图、平铺图或商品静物图变成视频场景。若两类图片冲突，"
        "人物、场景与动作以分镜图为准，商品可见外观以产品参考图为准。"
    )
    return f"{role_guard}\n\n{request.prompt}"


def _cli_single_line_prompt(prompt: str) -> str:
    """Keep every shot through the Windows npm .CMD wrapper.

    Passing literal newlines through its %* forwarding truncates CLI arguments
    after the first line. Keep the stored plan untouched and flatten only the
    provider-bound transport copy.
    """
    return " ".join(prompt.splitlines())


class KlingOfficialCliAdapter:
    # taskTraceId is telemetry only. No cloud deduplication guarantee is declared.
    supports_submission_idempotency = False

    def __init__(
        self,
        settings: Settings,
        *,
        process_runner: Callable[[list[str], float], Awaitable[CliProcessResult]] | None = None,
    ) -> None:
        self.settings = settings
        self.executable = settings.kling_cli_executable
        self._process_runner = process_runner or self._run_process
        self._capabilities: ProviderCapabilities | None = None
        self._last_availability: ProviderAvailability | None = None
        self._capability_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._last_call = 0.0
        self._submit_semaphore = asyncio.Semaphore(settings.kling_cli_max_submit_concurrency)
        self._query_semaphore = asyncio.Semaphore(settings.kling_cli_max_query_concurrency)
        self._cache_path = settings.runner_root / "kling" / "capabilities.json"

    def get_provider_name(self) -> str:
        return self.settings.kling_cli_provider_name

    def _base_args(self) -> list[str]:
        return [
            "--quiet",
            "--skill-name",
            KLING_SKILL_NAME,
            "--skill-version",
            KLING_SKILL_VERSION,
        ]

    async def _throttle(self) -> None:
        minimum_interval = 1.0 / self.settings.kling_cli_global_qps
        async with self._rate_lock:
            now = time.monotonic()
            remaining = minimum_interval - (now - self._last_call)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_call = time.monotonic()

    async def _run_process(self, arguments: list[str], timeout_seconds: float) -> CliProcessResult:
        executable = shutil.which(self.executable) or self.executable
        creation_flags = 0x08000000 if os.name == "nt" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
            )
        except FileNotFoundError as exc:
            raise KlingCliError("KLING_CLI_NOT_INSTALLED", "Kling CLI is not installed") from exc
        except OSError as exc:
            raise KlingCliError(
                "KLING_CLI_PROCESS_FAILED",
                "Kling CLI process could not be started",
                retryable=True,
                details={"errorType": type(exc).__name__},
            ) from exc
        communication = asyncio.create_task(process.communicate())
        try:
            # Cancelling communicate() on timeout can discard already-read output.
            # Keep its buffers alive until the killed child has been drained.
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communication), timeout_seconds
            )
        except TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = await communication
            handle, _, _, _ = _submission_evidence(
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
            raise KlingCliError(
                "KLING_CLI_TIMEOUT",
                "Kling CLI command timed out",
                retryable=True,
                uncertain=True,
                provider_task_id=handle,
            ) from exc
        except asyncio.CancelledError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await communication
            raise
        return CliProcessResult(
            exit_code=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def _invoke(
        self, command: str, arguments: list[str] | None = None, *,
        timeout_ms: int | None = None, submitted: bool = False,
        receipt: KlingSubmissionReceipt | None = None,
    ) -> dict[str, Any]:
        if receipt is None:
            return await self._invoke_once(
                command, arguments, timeout_ms=timeout_ms, submitted=submitted,
            )
        had_receipt = receipt.path.exists()
        try:
            previous = receipt.begin()
        except (OSError, ValueError) as exc:
            raise KlingCliError(
                "KLING_SUBMISSION_RECORD_FAILED",
                "Cannot persist the local CLI submission boundary; no new task submitted",
                submitted=had_receipt or isinstance(exc, ValueError),
                uncertain=had_receipt or isinstance(exc, ValueError),
            ) from exc
        if previous is not None:
            raise KlingCliError(
                "KLING_SUBMISSION_RECOVERY_REQUIRED",
                "An existing CLI invocation must be reconciled, never submitted again",
                provider_task_id=previous.get("providerTaskId"), submitted=True, uncertain=True,
            )
        try:
            body = await self._invoke_once(
                command, arguments, timeout_ms=timeout_ms, submitted=submitted,
            )
        except KlingCliError as exc:
            status = (
                "UNCERTAIN" if exc.submitted or exc.uncertain else
                "REJECTED" if exc.details.get("verifiedRejected") else "FAILED_BEFORE_SUBMIT"
            )
            try:
                receipt.finish(status, task_id=exc.provider_task_id, error_code=exc.code)
            except (OSError, ValueError):
                # Retain the original handle in memory so the DB can still save it.
                exc.details["receiptWriteFailed"] = True
            raise
        task_id = body.get("generationId") or body.get("generation_id")
        task_id = task_id if isinstance(task_id, str) and task_id.strip() else None
        credits = body.get("creditsConsumed", body.get("credits_consumed"))
        try:
            receipt.finish(
                "ACCEPTED" if task_id else "UNCERTAIN", task_id=task_id,
                credits=credits if type(credits) is int and credits >= 0 else None,
            )
        except (OSError, ValueError) as exc:
            raise KlingCliError(
                "KLING_SUBMISSION_RECEIPT_WRITE_FAILED",
                "CLI response received but local receipt persistence failed",
                provider_task_id=task_id, submitted=True, uncertain=True,
            ) from exc
        return body

    async def _invoke_once(
        self,
        command: str,
        arguments: list[str] | None = None,
        *,
        timeout_ms: int | None = None,
        submitted: bool = False,
    ) -> dict[str, Any]:
        await self._throttle()
        timeout = (timeout_ms or self.settings.kling_cli_command_timeout_ms) / 1000
        try:
            result = await self._process_runner(
                [*self._base_args(), command, *(arguments or [])],
                timeout,
            )
        except KlingCliError as exc:
            if exc.code == "KLING_CLI_TIMEOUT" and not submitted:
                exc.submitted = exc.uncertain = False
            raise
        handle, rejected, ambiguous, accepted = _submission_evidence(result.stdout, result.stderr)
        if submitted and ambiguous:
            raise KlingCliError(
                "KLING_SUBMIT_RESULT_CONFLICT",
                "Submission output has conflicting or uninspectable task evidence",
                submitted=True,
                uncertain=True,
                details={"verifiedRejected": False},
            )
        if result.exit_code != 0:
            lowered = f"{result.stdout}\n{result.stderr}".lower()
            preflight_failure = not accepted and not ambiguous and not result.stdout.strip() and (
                result.stderr.strip().startswith((
                    "MCP tool call failed (who_am_i):", "MCP 工具调用失败 (who_am_i)：",
                ))
            )
            if any(
                marker in lowered
                for marker in (
                    "not logged",
                    "login required",
                    "unauthorized",
                    "invalid_grant",
                    "登录态已失效",
                )
            ):
                code = "KLING_CLI_NOT_AUTHENTICATED"
            elif is_credit_rejection(lowered):
                code = "KLING_INSUFFICIENT_CREDITS"
            elif rejected or any(
                marker in lowered for marker in ("rate limit", "too many requests", "限流")
            ):
                code = "KLING_RATE_LIMITED"
            else:
                code = "KLING_CLI_PROCESS_FAILED"
            uncertain = submitted and not rejected and not preflight_failure and (
                code != "KLING_INSUFFICIENT_CREDITS" or accepted
            )
            raise KlingCliError(
                code,
                "Kling CLI command failed",
                retryable=code in {"KLING_RATE_LIMITED", "KLING_CLI_PROCESS_FAILED"},
                details={
                    "exitCode": result.exit_code,
                    "httpStatus": 429 if rejected else None,
                    "verifiedRejected": rejected,
                    "phase": "capability_lookup" if preflight_failure else command,
                    "generationInvoked": False if preflight_failure else None,
                },
                provider_task_id=handle,
                submitted=uncertain,
                uncertain=uncertain,
            )
        try:
            body = unwrap_cli_body(parse_cli_json(result.stdout), submitted=submitted)
            if submitted and handle and not (body.get("generationId") or body.get("generation_id")):
                raise KlingCliError(
                    "KLING_SUBMIT_RESULT_UNKNOWN",
                    "Final response lacks a handle; reconcile the earlier explicit task receipt",
                    provider_task_id=handle,
                    submitted=True,
                    uncertain=True,
                )
            return body
        except KlingCliError as exc:
            exc.provider_task_id = handle
            if submitted:
                if exc.details.get("verifiedRejected") and not rejected:
                    exc.args = (
                        "Contradictory submit evidence; reconcile without resubmitting",
                    )
                exc.details["verifiedRejected"] = rejected
                exc.submitted = exc.uncertain = not rejected and (
                    exc.code != "KLING_INSUFFICIENT_CREDITS" or accepted
                )
            raise

    async def _version(self) -> str:
        await self._throttle()
        result = await self._process_runner(
            ["--version", "--skill-name", KLING_SKILL_NAME, "--skill-version", KLING_SKILL_VERSION],
            self.settings.kling_cli_command_timeout_ms / 1000,
        )
        if result.exit_code != 0:
            raise KlingCliError("KLING_CLI_PROCESS_FAILED", "Unable to read Kling CLI version")
        return result.stdout.strip() or result.stderr.strip()

    def _clear_capability_cache(self) -> None:
        self._capabilities = None
        self._cache_path.unlink(missing_ok=True)

    async def login(self) -> ProviderAvailability:
        if not self.settings.kling_cli_enabled:
            raise KlingCliError(
                "KLING_CAPABILITY_UNSUPPORTED",
                "Kling CLI Provider is disabled",
            )
        self._clear_capability_cache()
        await self._invoke("login", timeout_ms=300_000)
        availability = await self.check_availability()
        if availability.status != "KLING_CLI_READY":
            raise KlingCliError(
                availability.status,
                availability.reason or "Kling CLI login could not be verified",
            )
        await self.refresh_capabilities(force=True)
        return availability

    async def logout(self) -> ProviderAvailability:
        version = await self._version()
        await self._invoke("logout")
        self._clear_capability_cache()
        self._last_availability = ProviderAvailability(
            status="KLING_CLI_NOT_AUTHENTICATED",
            available=True,
            authenticated=False,
            reason="Kling account is logged out",
            cli_version=version,
        )
        return self._last_availability

    async def account_credits(self) -> int:
        """Read member credits for a warning only. Never called on submit path."""
        body = await self._invoke("account")
        credits = body.get("availableRemainCredits")
        if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
            raise KlingCliError(
                "KLING_CLI_OUTPUT_PARSE_FAILED", "Kling account has no valid credit balance"
            )
        return credits

    async def check_availability(self) -> ProviderAvailability:
        if not self.settings.kling_cli_enabled:
            self._last_availability = ProviderAvailability(
                status="KLING_CAPABILITY_UNSUPPORTED",
                available=False,
                authenticated=False,
                reason="Kling CLI Provider is disabled",
            )
            return self._last_availability
        try:
            version = await self._version()
            await self._invoke("who_am_i")
        except KlingCliError as exc:
            self._last_availability = ProviderAvailability(
                status=exc.code,
                available=exc.code != "KLING_CLI_NOT_INSTALLED",
                authenticated=exc.code != "KLING_CLI_NOT_AUTHENTICATED",
                reason=str(exc),
            )
            return self._last_availability
        self._last_availability = ProviderAvailability(
            status="KLING_CLI_READY",
            available=True,
            authenticated=True,
            cli_version=version,
        )
        return self._last_availability

    def availability_snapshot(self) -> ProviderAvailability:
        if self._last_availability is not None:
            return self._last_availability
        cached = self._capabilities or self._load_cached_capabilities()
        if cached is not None:
            return ProviderAvailability(
                status="KLING_CLI_READY",
                available=True,
                authenticated=cached.authenticated,
                cli_version=cached.cli_version,
            )
        return ProviderAvailability(
            status=(
                "KLING_CAPABILITY_FETCH_FAILED"
                if self.settings.kling_cli_enabled
                else "KLING_CAPABILITY_UNSUPPORTED"
            ),
            available=False,
            authenticated=False,
            reason=(
                "Kling capability discovery has not completed"
                if self.settings.kling_cli_enabled
                else "Kling CLI Provider is disabled"
            ),
        )

    def capabilities_snapshot(self) -> ProviderCapabilities | None:
        return self._capabilities or self._load_cached_capabilities()

    def _load_cached_capabilities(self) -> ProviderCapabilities | None:
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            checked_at = datetime.fromisoformat(str(payload["checkedAt"]))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
            return ProviderCapabilities(
                cli_version=str(payload["cliVersion"]),
                checked_at=checked_at,
                authenticated=bool(payload["authenticated"]),
                account_type=payload.get("accountType"),
                supported_models=tuple(payload.get("supportedModels") or ()),
                supported_tools=tuple(payload.get("supportedTools") or ()),
                video_durations=tuple(payload.get("videoDurations") or ()),
                aspect_ratios=tuple(payload.get("aspectRatios") or ()),
                image_input_limit=int(payload.get("imageInputLimit") or 1),
                supports_tail_image=bool(payload.get("supportsTailImage")),
                supports_multiple_images=bool(payload.get("supportsMultipleImages")),
                supports_watermark_free_output=bool(payload.get("supportsWatermarkFreeOutput")),
                supports_sound_control=bool(payload.get("supportsSoundControl")),
                raw_capabilities=dict(payload.get("rawCapabilities") or {}),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _save_capabilities(self, capabilities: ProviderCapabilities) -> None:
        payload = {
            "cliVersion": capabilities.cli_version,
            "checkedAt": capabilities.checked_at.isoformat(),
            "authenticated": capabilities.authenticated,
            "accountType": capabilities.account_type,
            "supportedModels": list(capabilities.supported_models),
            "supportedTools": list(capabilities.supported_tools),
            "videoDurations": list(capabilities.video_durations),
            "aspectRatios": list(capabilities.aspect_ratios),
            "imageInputLimit": capabilities.image_input_limit,
            "supportsTailImage": capabilities.supports_tail_image,
            "supportsMultipleImages": capabilities.supports_multiple_images,
            "supportsWatermarkFreeOutput": capabilities.supports_watermark_free_output,
            "supportsSoundControl": capabilities.supports_sound_control,
            "rawCapabilities": capabilities.raw_capabilities,
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._cache_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._cache_path)

    async def refresh_capabilities(self, *, force: bool = False) -> ProviderCapabilities:
        now = datetime.now(UTC)
        ttl = timedelta(seconds=self.settings.kling_cli_capability_cache_seconds)
        cached = self._capabilities or self._load_cached_capabilities()
        if cached and now - cached.checked_at < ttl and not force:
            self._capabilities = cached
            return cached
        async with self._capability_lock:
            try:
                version, identity, tools = await asyncio.gather(
                    self._version(),
                    self._invoke("who_am_i"),
                    self._invoke("tool_list"),
                )
            except KlingCliError as exc:
                if cached and now - cached.checked_at < ttl:
                    self._capabilities = cached
                    return cached
                raise KlingCliError(
                    "KLING_CAPABILITY_FETCH_FAILED",
                    str(exc),
                    retryable=exc.retryable,
                ) from exc
            model_items = _video_model_items(
                ProviderCapabilities(
                    cli_version=version,
                    checked_at=now,
                    authenticated=True,
                    account_type=None,
                    supported_models=(),
                    supported_tools=(),
                    raw_capabilities=identity,
                )
            )
            models = tuple(
                dict.fromkeys(
                    str(item["model"]) for item in model_items if isinstance(item.get("model"), str)
                )
            )
            argument_specs = [
                argument
                for item in model_items
                for argument in (item.get("arguments") or [])
                if isinstance(argument, dict)
            ]
            profiles = [
                _model_input_profile(
                    ProviderCapabilities(
                        cli_version=version,
                        checked_at=now,
                        authenticated=True,
                        account_type=None,
                        supported_models=models,
                        supported_tools=(),
                        raw_capabilities=identity,
                    ),
                    model,
                )
                for model in models
            ]
            tail = any(profile[1] for profile in profiles)
            image_limit = max((profile[0] for profile in profiles), default=1)
            tool_items = tools.get("tools") if isinstance(tools.get("tools"), list) else []
            supported_tools = tuple(
                str(item["name"])
                for item in tool_items
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
            raw = {
                **identity,
                "tools": tool_items,
            }
            user = identity.get("user") if isinstance(identity.get("user"), dict) else {}
            capabilities = ProviderCapabilities(
                cli_version=version,
                checked_at=now,
                authenticated=True,
                account_type=str(user.get("accountType")) if user.get("accountType") else None,
                supported_models=models,
                supported_tools=supported_tools,
                video_durations=_all_allowed_values(argument_specs, "duration"),
                aspect_ratios=_all_allowed_values(argument_specs, "aspect_ratio", "aspectRatio"),
                image_input_limit=image_limit,
                supports_tail_image=tail,
                supports_multiple_images=image_limit > 1,
                supports_watermark_free_output=True,
                supports_sound_control=any(
                    str(item.get("name") or "").replace("_", "").lower()
                    in {"sound", "enablesound", "enableaudio"}
                    for item in argument_specs
                ),
                raw_capabilities=raw,
            )
            previous = self._capabilities
            self._capabilities = capabilities
            self._last_availability = ProviderAvailability(
                status="KLING_CLI_READY",
                available=True,
                authenticated=True,
                cli_version=version,
            )
            self._save_capabilities(capabilities)
            if previous and asdict(previous) != asdict(capabilities):
                logger.warning("Kling CLI capabilities changed")
            return capabilities

    def _select_model(
        self,
        capabilities: ProviderCapabilities,
        preferred: str | None,
        request: VideoGenerationRequest,
    ) -> str:
        if preferred:
            if preferred not in capabilities.supported_models:
                raise KlingCliError(
                    "KLING_CAPABILITY_UNSUPPORTED",
                    f"Configured Kling model is unavailable: {preferred}",
                )
            models_to_check = (preferred,)
        else:
            models_to_check = capabilities.supported_models
        candidates: list[str] = []
        parameter_compatible = False
        role_compatible = False
        total_image_count = len(request.image_paths) + len(request.product_reference_paths)
        for model in models_to_check:
            arguments = _argument_specs(capabilities, model)
            durations = _allowed_values(arguments, "duration")
            ratios = _allowed_values(arguments, "aspect_ratio", "aspectRatio")
            if (
                request.duration is not None
                and durations
                and str(request.duration) not in durations
            ):
                continue
            if request.aspect_ratio and ratios and request.aspect_ratio not in ratios:
                continue
            parameter_compatible = True
            input_limit, supports_tail, supports_repeated_images = _model_input_profile(
                capabilities, model
            )
            if request.product_reference_paths and not supports_repeated_images:
                continue
            role_compatible = True
            if total_image_count > input_limit and not (
                not request.product_reference_paths
                and request.fallback_mode == "first_last_frame"
                and supports_tail
            ):
                continue
            candidates.append(model)
        if not candidates:
            if parameter_compatible and request.product_reference_paths and not role_compatible:
                raise KlingCliError(
                    "KLING_PRODUCT_REFERENCE_UNSUPPORTED",
                    "Compatible Kling models cannot keep product references separate from "
                    "storyboard inputs",
                )
            if parameter_compatible:
                largest_limit = max(
                    (_model_input_profile(capabilities, model)[0] for model in models_to_check),
                    default=1,
                )
                if request.product_reference_paths:
                    raise KlingCliError(
                        "KLING_IMAGE_INPUT_LIMIT_EXCEEDED",
                        "Compatible Kling models support at most "
                        f"{largest_limit} total storyboard and product reference image input(s)",
                    )
                raise KlingCliError(
                    "KLING_MULTI_STORYBOARD_UNSUPPORTED",
                    "Compatible Kling models support at most "
                    f"{largest_limit} storyboard image input(s)",
                )
            raise KlingCliError(
                "KLING_CAPABILITY_UNSUPPORTED",
                "Current Kling account has no image-to-video model compatible with this request",
            )
        return candidates[0]

    async def validate_request(self, request: VideoGenerationRequest) -> str:
        if not request.prompt.strip():
            raise KlingCliError("KLING_INVALID_INPUT", "Video prompt cannot be empty")
        if not request.image_paths:
            raise KlingCliError("KLING_INVALID_INPUT", "At least one storyboard image is required")
        capabilities = await self.refresh_capabilities()
        model = self._select_model(capabilities, request.model, request)
        if request.director_native_profile in _OOTD_PROFILES:
            specs = _argument_specs(capabilities, model)
            names = {str(item.get("name") or "").replace("_", "").lower()
                     for item in specs}
            if (
                model != "kling-video-v3_0_omni"
                or not 1 <= len(request.image_paths) <= 7
                or request.duration is None or not 5 <= request.duration <= 15
                or request.audio != "off" or request.multi_shot is not True
                or not {"duration", "enableaudio", "prefermultishots"} <= names
                or (_allowed_values(specs, "enable_audio")
                    and "false" not in _allowed_values(specs, "enable_audio"))
                or (_allowed_values(specs, "prefer_multi_shots")
                    and "true" not in _allowed_values(specs, "prefer_multi_shots"))
            ):
                raise KlingCliError(
                    "OOTD_CLI_CONTRACT_UNSUPPORTED",
                    "OOTD requires ordered references, Omni, explicit duration, "
                    "silent multi-shot; "
                    "no alternative provider will be submitted",
                )
        if model in _omni_models(capabilities) and request.element_references:
            raise KlingCliError(
                "KLING_ELEMENTS_UNSUPPORTED",
                "CLI Element bindings need verified type data; "
                "use image references or official API",
            )
        limit, supports_tail, supports_repeated_images = _model_input_profile(capabilities, model)
        total_image_count = len(request.image_paths) + len(request.product_reference_paths)
        if request.product_reference_paths and not supports_repeated_images:
            raise KlingCliError(
                "KLING_PRODUCT_REFERENCE_UNSUPPORTED",
                "Selected Kling model cannot keep product references separate from "
                "storyboard inputs",
            )
        if total_image_count > limit and not (
            not request.product_reference_paths
            and request.fallback_mode == "first_last_frame"
            and supports_tail
        ):
            kind = (
                "storyboard and product reference"
                if request.product_reference_paths
                else "storyboard"
            )
            raise KlingCliError(
                (
                    "KLING_IMAGE_INPUT_LIMIT_EXCEEDED"
                    if request.product_reference_paths
                    else "KLING_MULTI_STORYBOARD_UNSUPPORTED"
                ),
                f"Selected Kling model supports at most {limit} total {kind} image input(s)",
            )
        if request.fallback_mode not in {"disabled", "first_last_frame"}:
            raise KlingCliError("KLING_INVALID_INPUT", "Invalid Kling storyboard fallback mode")
        for path in request.image_paths:
            if not path.is_file():
                raise KlingCliError(
                    "KLING_INVALID_INPUT", f"Storyboard image is unavailable: {path.name}"
                )
        for path in request.product_reference_paths:
            if not path.is_file():
                raise KlingCliError(
                    "KLING_INVALID_INPUT",
                    f"Product reference image is unavailable: {path.name}",
                )
        return model

    async def _upload_references(self, arguments: list[str]) -> None:
        uploaded: dict[str, str] = {}
        try:
            # Bound the entire preparation stage, including serial uploads.
            async with asyncio.timeout(self.settings.kling_cli_command_timeout_ms / 1000):
                for index, argument in enumerate(arguments[:-1]):
                    if argument not in {"--image", "--tailImage"}:
                        continue
                    source = arguments[index + 1]
                    if source not in uploaded:
                        upload = await self._invoke("file_upload", [source])
                        url = upload.get("url")
                        if not isinstance(url, str) or not url.startswith("https://"):
                            raise KlingCliError(
                                "KLING_UPLOAD_INVALID_RESPONSE",
                                "Kling upload did not return a valid HTTPS URL",
                            )
                        uploaded[source] = url
                    arguments[index + 1] = uploaded[source]
        except (KlingCliError, TimeoutError) as exc:
            raise KlingCliError(
                "KLING_UPLOAD_FAILED",
                "Reference upload failed before video submission; no video submitted",
                retryable=getattr(exc, "retryable", True),
                details={"phase": "upload", "causeCode": getattr(exc, "code", "UPLOAD_TIMEOUT"),
                         "generationInvoked": False},
            ) from exc

    async def submit(self, request: VideoGenerationRequest) -> ProviderSubmissionResult:
        model = await self.validate_request(request)
        capabilities = await self.refresh_capabilities()
        paths = list(request.image_paths)
        product_paths = list(request.product_reference_paths)
        input_limit, supports_tail, supports_repeated_images = _model_input_profile(
            capabilities, model
        )
        if len(paths) > input_limit:
            paths = [paths[0], paths[-1]]
        arguments = ["--model", model]
        if supports_repeated_images:
            for path in [*paths, *product_paths]:
                arguments.extend(["--image", str(path)])
        else:
            arguments.extend(["--image", str(paths[0])])
            if len(paths) > 1:
                if not supports_tail:
                    raise KlingCliError(
                        "KLING_MULTI_STORYBOARD_UNSUPPORTED",
                        "Selected Kling model does not expose a tail-image input",
                    )
                arguments.extend(["--tailImage", str(paths[-1])])
        specs = _argument_specs(capabilities, model)
        spec_names = {str(item.get("name") or "").replace("_", "").lower() for item in specs}
        if request.duration is not None and "duration" in spec_names:
            arguments.extend(["--duration", str(request.duration)])
        if request.aspect_ratio and "aspectratio" in spec_names:
            arguments.extend(["--aspectRatio", request.aspect_ratio])
        # Resolution is the cost tier on Kling (720p=std, 1080p=pro). Dropping it
        # silently billed every job at the model default, so an operator who
        # selected 720p was still charged 1080p. Never let the request value win
        # over the model declaration.
        if request.resolution and "resolution" in spec_names:
            allowed_resolution = _allowed_values(specs, "resolution")
            if allowed_resolution and request.resolution not in allowed_resolution:
                raise KlingCliError(
                    "KLING_RESOLUTION_UNSUPPORTED",
                    f"Selected Kling model {model} does not allow "
                    f"resolution={request.resolution}",
                )
            arguments.extend(["--resolution", request.resolution])
        # Kling defaults enable_audio to true on Omni models. Only ask for synced
        # audio when the caller explicitly selects it; "off" and "original" must
        # never inherit that default.
        if "enableaudio" in spec_names:
            allowed_audio = _allowed_values(specs, "enable_audio")
            wants_native_audio = request.audio == "native"
            requested_audio = "true" if wants_native_audio else "false"
            if allowed_audio and requested_audio not in allowed_audio:
                raise KlingCliError(
                    "KLING_AUDIO_ENABLE_UNSUPPORTED"
                    if wants_native_audio
                    else "KLING_AUDIO_DISABLE_UNSUPPORTED",
                    f"Selected Kling model {model} cannot set "
                    f"enable_audio={requested_audio}",
                )
            arguments.extend(["--enable_audio", requested_audio])
        # "original" keeps the source video's own sound, which is mutually
        # exclusive with synced audio above.
        if request.audio == "original" and "keeporiginalsound" in spec_names:
            arguments.extend(["--keepOriginalSound", "true"])
        if request.multi_shot is not None:
            # Do not rely on the CLI default when the planner requests cuts.
            if "prefermultishots" not in spec_names:
                raise KlingCliError(
                    "KLING_MULTI_SHOT_UNSUPPORTED",
                    f"Selected Kling model {model} does not declare prefer_multi_shots",
                )
            requested_shots = "true" if request.multi_shot else "false"
            allowed_shots = _allowed_values(specs, "prefer_multi_shots")
            if allowed_shots and requested_shots not in allowed_shots:
                raise KlingCliError(
                    "KLING_MULTI_SHOT_UNSUPPORTED",
                    f"Selected Kling model {model} does not allow "
                    f"prefer_multi_shots={requested_shots}",
                )
            arguments.extend(["--prefer_multi_shots", requested_shots])
        if request.task_trace_id:
            arguments.extend(["--task-trace-id", request.task_trace_id])
        provider_prompt = _role_bound_prompt(request)
        # Kling 3.0 Omni CLI uses 图片N references for ordered --image slots.
        # This mapping must not depend on cut wording or multi-shot preference.
        multi_ootd = (
            request.director_native_profile in _OOTD_PROFILES
            and (len(paths) > 1 or len(product_paths) > 1)
        )
        if (model == "kling-video-v3_0_omni"
                and (request.director_native_profile not in _OOTD_PROFILES
                     or multi_ootd)):
            import re

            image_count = len(paths) + len(product_paths)

            def as_kling_ref(match):
                number = int(match.group(1))
                if not 1 <= number <= image_count:
                    raise KlingCliError(
                        "KLING_IMAGE_REFERENCE_UNAVAILABLE",
                        f"Native prompt references unbound image: @image_{number}",
                    )
                return f"图片{number}"

            provider_prompt = re.sub(r"@image_(\d+)\b", as_kling_ref, provider_prompt)
        if multi_ootd and len(provider_prompt) > 3072:
            raise KlingCliError(
                "KLING_PROMPT_TOO_LONG",
                "OOTD multi-image prompt plus image-role header exceeds 3072 characters",
            )
        arguments.append(_cli_single_line_prompt(provider_prompt))
        async with self._submit_semaphore:
            # Upload before entering the paid command. The CLI otherwise uploads
            # references concurrently inside generation, obscuring the failure stage.
            await self._upload_references(arguments)
            receipt = None
            if request.submission_operation_key:
                if not request.submission_account_id or not request.submission_fingerprint:
                    raise KlingCliError(
                        "KLING_SUBMISSION_RECORD_FAILED",
                        "Missing account or input identity for the CLI receipt",
                    )
                receipt = KlingSubmissionReceipt(
                    self.settings.runner_root, request.submission_operation_key,
                    self.get_provider_name(), request.submission_account_id,
                    request.submission_fingerprint,
                )
            # A verified HTTP 429 rejects the request before it becomes a paid job.
            # Never retry a text-only limit, lost process, or missing generationId.
            for retry_index in range(2):
                try:
                    body = await self._invoke(
                        "omni_ref_video"
                        if model in _omni_models(capabilities)
                        else "image_to_video",
                        arguments,
                        timeout_ms=self.settings.kling_cli_command_timeout_ms,
                        submitted=True,
                        receipt=receipt,
                    )
                    break
                except KlingCliError as exc:
                    if (
                        retry_index
                        or exc.code != "KLING_RATE_LIMITED"
                        or exc.submitted
                        or exc.uncertain
                        or exc.details.get("verifiedRejected") is not True
                    ):
                        raise
                    await asyncio.sleep(max(2.0, exc.retry_after_seconds or 0))
        task_id = body.get("generationId") or body.get("generation_id")
        if not isinstance(task_id, str) or not task_id:
            raise KlingCliError(
                "KLING_SUBMIT_RESULT_UNKNOWN",
                "Kling CLI did not return generationId",
                retryable=False,
                submitted=True,
                uncertain=True,
            )
        credits = body.get("creditsConsumed")
        if credits is None:
            credits = body.get("credits_consumed")
        return ProviderSubmissionResult(
            provider_task_id=task_id,
            raw_status=str(body.get("status") or "submitted"),
            credits_consumed=(
                int(credits)
                if isinstance(credits, (int, float))
                and not isinstance(credits, bool)
                and credits >= 0
                else None
            ),
            provider_model=model,
            raw_response=body,
        )

    async def query(self, provider_task_id: str) -> ProviderTaskResult:
        if not provider_task_id:
            raise KlingCliError("KLING_INVALID_INPUT", "generationId is required")
        async with self._query_semaphore:
            body = await self._invoke(
                "query_tasks",
                [provider_task_id],
                timeout_ms=self.settings.kling_cli_query_timeout_ms,
            )
        raw_status = str(body.get("status") or "")
        status = map_kling_status(raw_status)
        works = body.get("works")
        outputs: list[ProviderOutput] = []
        if isinstance(works, list):
            for work in works:
                if not isinstance(work, dict):
                    continue
                clean_url = work.get("urlWithoutWatermark")
                if not isinstance(clean_url, str) or not clean_url:
                    clean_url = None
                url = clean_url or work.get("url")
                if isinstance(url, str) and url:
                    outputs.append(
                        ProviderOutput(
                            url=url,
                            content_type=str(work.get("contentType"))
                            if work.get("contentType")
                            else None,
                            url_without_watermark=clean_url,
                            cover_url=str(work.get("coverUrl")) if work.get("coverUrl") else None,
                        )
                    )
        if status in {"succeeded", "partial"} and not outputs:
            # MCP may publish COMPLETED before the CDN work URL is populated.
            # Continue querying the SAME generationId; never mark the paid job
            # uncertain or attempt a second paid submission for this interval.
            status = "running"
        return ProviderTaskResult(
            status=status,
            raw_status=raw_status,
            outputs=tuple(outputs),
            raw_response=body,
        )

    async def submission_account_id(self) -> str:
        """Fresh, non-secret identity; never use cached identity after account switching."""
        identity = await self._invoke("who_am_i")
        user = identity.get("user") or {}
        account_id = user.get("userId") or user.get("id")
        if not account_id:
            raise KlingCliError("KLING_ACCOUNT_UNVERIFIED", "Kling account identity is unavailable")
        return hashlib.sha256(str(account_id).encode()).hexdigest()

    async def find_recent_submission(self, **_: Any) -> ProviderSubmissionMatch | None:
        """The CLI exposes point lookup only, not an account history endpoint.

        Returning no match is deliberate: a local retry must never infer a cloud
        task from timestamps or prompt text when the provider supplies no evidence.
        """
        raise KlingCliError(
            "KLING_HISTORY_QUERY_UNSUPPORTED",
            "Kling CLI does not expose an evidence-based historical task lookup",
            retryable=False,
        )

    async def download(self, output: ProviderOutput, destination: Path) -> DownloadResult:
        from libtv_agent.adapters.download_worker import stage_download

        existing_size = await asyncio.to_thread(
            lambda: destination.stat().st_size if destination.is_file() else 0
        )
        if existing_size > 0:
            return DownloadResult(path=destination, reused=True, size_bytes=existing_size)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        destination.parent.mkdir(parents=True, exist_ok=True)

        def _download() -> int:
            download_url = output.url_without_watermark or output.url
            request = Request(download_url, headers={"User-Agent": "libtv-agent-kling-cli/1"})
            with urlopen(
                request, timeout=self.settings.kling_cli_download_timeout_ms / 1000
            ) as response:
                status = int(getattr(response, "status", 200))
                if status < 200 or status >= 300:
                    raise OSError(f"unexpected HTTP status {status}")
                content_type = str(response.headers.get("Content-Type") or "")
                if content_type and not (
                    content_type.startswith("video/") or content_type == "application/octet-stream"
                ):
                    raise OSError("downloaded Kling output is not a video")
                with temporary.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                declared_length = response.headers.get("Content-Length")
            size = temporary.stat().st_size
            if declared_length is not None and size != int(declared_length):
                raise OSError("downloaded output length does not match Content-Length")
            if size <= 0:
                raise OSError("downloaded Kling output is empty")
            return size

        try:
            size = await stage_download(
                _download,
                temporary,
                destination,
                timeout=self.settings.kling_cli_download_timeout_ms / 1000,
            )
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise KlingCliError(
                "KLING_DOWNLOAD_FAILED",
                "Kling output download failed",
                retryable=True,
                details={"errorType": type(exc).__name__},
            ) from exc
        return DownloadResult(path=destination, reused=False, size_bytes=size)

    async def download_media(self, *, url: str, destination: Path) -> Path:
        return (
            await self.download(ProviderOutput(url=url, content_type="video"), destination)
        ).path

    async def get_account_status(self) -> dict[str, Any]:
        return await self._invoke("account")


async def retry_kling_call(
    operation: Callable[[], Awaitable[Any]], *, attempts: int = 3, base_delay: float = 1.0
) -> Any:
    for index in range(attempts):
        try:
            return await operation()
        except KlingCliError as exc:
            if not exc.retryable or exc.submitted or exc.uncertain or index == attempts - 1:
                raise
            delay = max(
                base_delay * (2**index) + random.uniform(0, base_delay),
                exc.retry_after_seconds or 0,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
