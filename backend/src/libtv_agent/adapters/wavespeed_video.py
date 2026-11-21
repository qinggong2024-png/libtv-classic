from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from libtv_agent.adapters.kling_official_cli import KlingCliError
from libtv_agent.adapters.video_provider import (
    DownloadResult,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderOutput,
    ProviderSubmissionResult,
    ProviderTaskResult,
    VideoGenerationRequest,
)
from libtv_agent.adapters.wavespeed import WaveSpeedAdapter, WaveSpeedError
from libtv_agent.config import Settings
from libtv_agent.services.deep_video_media import (
    DEPTH_MODEL,
    FLASH_RECREATE_MODEL,
    LEGACY_RECREATE_MODEL,
    inspect_reference_video,
    is_deep_video_model,
    is_recreate_video_model,
    prepare_flash_edit_input,
)
from libtv_agent.services.generation_safety import paid_generation_authorized

logger = logging.getLogger(__name__)
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_TERMINAL_FAILURES = frozenset({"failed", "cancelled", "timeout", "deleted"})


class WaveSpeedVideoError(KlingCliError):
    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(code, message, **kwargs)
        self.provider = "wavespeed_video"


def _translate_error(exc: WaveSpeedError) -> WaveSpeedVideoError:
    return WaveSpeedVideoError(
        exc.code,
        str(exc),
        retryable=exc.retryable,
        provider_task_id=exc.task_id,
        details={**exc.details, **({"modelId": exc.model_id} if exc.model_id else {})},
        submitted=exc.submitted,
        uncertain=exc.uncertain,
    )


def _provider_status(raw_status: str) -> str:
    if raw_status == "created":
        return "queued"
    if raw_status == "processing":
        return "running"
    if raw_status == "completed":
        return "succeeded"
    if raw_status in _TERMINAL_FAILURES:
        return "failed"
    return "unknown"


def _is_probably_video_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return (
        len(header) >= 8
        and header[4:8] == b"ftyp"
        or header.startswith(b"\x1aE\xdf\xa3")
        or header.startswith(b"RIFF")
        and header[8:12] == b"AVI "
    )


class WaveSpeedVideoAdapter:
    """VideoProviderAdapter for WaveSpeed Kling O3 reference-to-video models."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: WaveSpeedAdapter | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or WaveSpeedAdapter(settings)
        self._availability: ProviderAvailability | None = None
        self._capabilities: ProviderCapabilities | None = None

    def get_provider_name(self) -> str:
        return self.settings.wavespeed_video_provider_name

    def availability_snapshot(self) -> ProviderAvailability:
        # The image and video routes share one WaveSpeed client in the API app.
        # Always reflect its latest administrator verification instead of a
        # stale wrapper-level snapshot captured before the verification ran.
        value = self._client.availability_snapshot()
        return ProviderAvailability(
            status=value.status,
            available=value.available,
            authenticated=value.authenticated,
            reason=value.reason,
            cli_version="wavespeed-api-v3",
        )

    def capabilities_snapshot(self) -> ProviderCapabilities | None:
        return self._capabilities

    async def check_availability(self) -> ProviderAvailability:
        value = await self._client.verify_credentials()
        self._availability = ProviderAvailability(
            status=value.status,
            available=value.available,
            authenticated=value.authenticated,
            reason=value.reason,
            cli_version="wavespeed-api-v3",
        )
        return self._availability

    async def refresh_capabilities(self, *, force: bool = False) -> ProviderCapabilities:
        if force:
            self._availability = None
            self._capabilities = None
        availability = await self.check_availability()
        if not availability.available:
            raise WaveSpeedVideoError(
                availability.status,
                availability.reason or "WaveSpeed video API verification failed",
            )
        if self._capabilities is None:
            self._capabilities = ProviderCapabilities(
                cli_version="wavespeed-api-v3",
                checked_at=datetime.now(UTC),
                authenticated=True,
                account_type=None,
                supported_models=(self.settings.wavespeed_video_model,),
                supported_tools=("reference_to_video", "prediction_query", "media_upload"),
                video_durations=tuple(str(value) for value in range(3, 16)),
                aspect_ratios=("16:9", "9:16", "1:1"),
                image_input_limit=7,
                supports_tail_image=False,
                supports_multiple_images=True,
                supports_watermark_free_output=True,
                supports_sound_control=True,
                raw_capabilities={
                    "apiVersion": "v3",
                    "modelId": self.settings.wavespeed_video_model,
                    "submitPath": f"/{self.settings.wavespeed_video_model}",
                    "queryPath": "/predictions/{id}/result",
                    "maxReferenceImages": 7,
                },
            )
        return self._capabilities

    async def login(self) -> ProviderAvailability:
        raise WaveSpeedVideoError(
            "WAVESPEED_LOGIN_UNSUPPORTED",
            "WaveSpeed uses an API key configured by an administrator",
        )

    async def logout(self) -> ProviderAvailability:
        raise WaveSpeedVideoError(
            "WAVESPEED_LOGOUT_UNSUPPORTED",
            "WaveSpeed API keys are cleared from the administrator configuration",
        )

    async def get_account_status(self) -> dict[str, Any]:
        availability = await self.check_availability()
        return {
            "provider": self.get_provider_name(),
            "status": availability.status,
            "configured": availability.authenticated,
        }

    @staticmethod
    def _image_paths(request: VideoGenerationRequest) -> tuple[Path, ...]:
        values = (
            *request.image_paths,
            *((request.first_frame_path,) if request.first_frame_path else ()),
            *((request.last_frame_path,) if request.last_frame_path else ()),
            *request.refer_image_paths,
            *request.product_reference_paths,
        )
        return tuple(dict.fromkeys(values))

    async def validate_request(self, request: VideoGenerationRequest) -> str:
        if is_deep_video_model(request.model):
            return await self._validate_deep_video(request)
        prompt = request.prompt.strip()
        if not prompt:
            raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "Video prompt cannot be empty")
        if len(prompt) > 10_000:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", "WaveSpeed video prompt cannot exceed 10000 characters"
            )
        model = (request.model or self.settings.wavespeed_video_model).strip()
        if not model.endswith("/reference-to-video"):
            raise WaveSpeedVideoError(
                "WAVESPEED_MODEL_UNSUPPORTED",
                "This workflow requires a WaveSpeed reference-to-video model",
            )
        images = self._image_paths(request)
        if not images:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", "WaveSpeed reference-to-video requires an image"
            )
        if len(images) > 7:
            raise WaveSpeedVideoError(
                "WAVESPEED_IMAGE_INPUT_LIMIT_EXCEEDED",
                "WaveSpeed Kling O3 accepts at most 7 reference images",
            )
        if request.element_references:
            raise WaveSpeedVideoError(
                "WAVESPEED_ELEMENTS_UNSUPPORTED",
                "Kling official element IDs cannot be used by the WaveSpeed video Provider",
            )
        if request.feature_video_url or request.base_video_url:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID",
                "The configured WaveSpeed reference-to-video workflow does not accept video input",
            )
        if request.duration is not None and request.duration not in range(3, 16):
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", "WaveSpeed video duration must be 3-15 seconds"
            )
        if request.aspect_ratio and request.aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", "Unsupported WaveSpeed video aspect ratio"
            )
        for path in images:
            await asyncio.to_thread(self._validate_image, path)
        return model

    async def _validate_deep_video(self, request: VideoGenerationRequest) -> str:
        if request.reference_video_path is None:
            raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "深度视频需要上传参考视频")
        if request.feature_video_url or request.base_video_url or request.element_references:
            raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "深度视频仅接受已上传的视频和图片")
        try:
            metadata = await inspect_reference_video(request.reference_video_path)
        except (ValueError, TimeoutError) as exc:
            raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", str(exc)) from exc
        images = self._image_paths(request)
        if request.model == DEPTH_MODEL:
            if images:
                raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "提取深度无需替换素材")
        else:
            if not request.prompt.strip() or len(request.prompt) > 10000:
                raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "请填写有效的复刻要求")
            if not 1 <= len(images) <= 5:
                raise WaveSpeedVideoError(
                    "WAVESPEED_INPUT_INVALID", "动作复刻需要 1–5 张目标参考图"
                )
            minimum, maximum = (5, 10) if request.model == LEGACY_RECREATE_MODEL else (4, 15)
            if (not minimum <= metadata.duration <= maximum
                    or request.duration not in range(minimum, maximum + 1)):
                raise WaveSpeedVideoError(
                    "WAVESPEED_INPUT_INVALID", f"人物与商品替换支持 {minimum}–{maximum} 秒视频",
                )
            if request.aspect_ratio not in {"9:16", "16:9"}:
                raise WaveSpeedVideoError("WAVESPEED_INPUT_INVALID", "深度动作复刻支持横屏或竖屏")
            for path in images:
                await asyncio.to_thread(self._validate_image, path)
        return str(request.model)

    def _validate_image(self, path: Path) -> None:
        if not path.is_file():
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", f"Reference image is unavailable: {path.name}"
            )
        if path.suffix.lower() not in _IMAGE_EXTENSIONS:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID", f"Unsupported reference image type: {path.name}"
            )
        size = path.stat().st_size
        if size <= 0 or size > self.settings.max_input_image_bytes:
            raise WaveSpeedVideoError(
                "WAVESPEED_INPUT_INVALID",
                f"Reference image exceeds the configured size limit: {path.name}",
            )

    async def _preflight_budget(self, model: str, payload: dict[str, object]) -> None:
        # Price must be quoted against the identical media URL and parameters
        # that will be submitted. Neither read-only call creates a prediction.
        if not (hasattr(self._client, "get_balance_usd")
                and hasattr(self._client, "quote_price_usd")):
            return  # Legacy injected test clients; real client always provides both.
        try:
            balance, price = await asyncio.gather(
                self._client.get_balance_usd(),
                self._client.quote_price_usd(model, payload),
            )
        except WaveSpeedError as exc:
            # A failed price check is NOT a model-price quote. The submission
            # endpoint remains authoritative, and its 4xx error is classified.
            logger.warning(
                "WaveSpeed preflight unavailable",
                extra={"provider": self.get_provider_name(), "error_code": exc.code},
            )
            return
        if price > balance + 0.0000001:
            raise WaveSpeedVideoError(
                "WAVESPEED_BALANCE_INSUFFICIENT",
                f"WaveSpeed 余额 ${balance:.4f}，本阶段预计需 ${price:.4f}，"
                f"尚差 ${price - balance:.4f}；本阶段未提交生成。",
                retryable=False,
                details={
                    "balanceUsd": round(balance, 4),
                    "estimatedPriceUsd": round(price, 4),
                    "shortfallUsd": round(price - balance, 4),
                    "modelId": model,
                },
            )

    async def _upload_flash_edit_input(self, request: VideoGenerationRequest) -> str:
        source = request.reference_video_path
        aspect_ratio = request.aspect_ratio
        assert source is not None and aspect_ratio is not None
        with TemporaryDirectory(prefix="classic-flash-") as directory:
            destination = Path(directory) / f"{source.stem}.mp4"
            try:
                await prepare_flash_edit_input(source, destination, aspect_ratio)
            except (OSError, TimeoutError, ValueError) as exc:
                raise WaveSpeedVideoError(
                    "WAVESPEED_INPUT_INVALID", "无法将深度视频转换为所选的 720p 画幅",
                ) from exc
            return await self._client.upload_media(destination)

    async def submit(self, request: VideoGenerationRequest) -> ProviderSubmissionResult:
        model = await self.validate_request(request)
        try:
            if is_deep_video_model(model):
                if (self.settings.app_env != "test"
                        and not paid_generation_authorized(self.settings)):
                    raise WaveSpeedVideoError(
                        "PAID_GENERATION_BLOCKED", "本站尚未授权真实付费生成",
                    )
                assert request.reference_video_path is not None
                if model == FLASH_RECREATE_MODEL:
                    video_url = await self._upload_flash_edit_input(request)
                else:
                    video_url = await self._client.upload_media(request.reference_video_path)
                payload: dict[str, object] = {"video": video_url}
                if model != DEPTH_MODEL:
                    images = await asyncio.gather(
                        *(self._client.upload_media(path) for path in self._image_paths(request))
                    )
                    if is_recreate_video_model(model):
                        payload.update({
                            "prompt": request.prompt.strip(), "reference_images": list(images),
                            "resolution": "720p", "generate_audio": False,
                        })
                        if model != FLASH_RECREATE_MODEL:
                            payload.update({
                                "duration": request.duration, "aspect_ratio": request.aspect_ratio,
                                "enable_web_search": False,
                            })
                    else:
                        payload.update({
                            "prompt": request.prompt.strip(), "images": list(images),
                            "task": "depth", "duration": request.duration,
                            "size": "720*1280" if request.aspect_ratio == "9:16" else "1280*720",
                        })
                await self._preflight_budget(model, payload)
                submission = await self._client.submit_prediction(
                    model, payload, validate_model=False,
                )
                return ProviderSubmissionResult(
                    provider_task_id=submission.task_id, raw_status=submission.status,
                    credits_consumed=None, provider_model=submission.model_id,
                    raw_response=submission.raw_response,
                )
            image_urls = await asyncio.gather(
                *(self._client.upload_media(path) for path in self._image_paths(request))
            )
            payload = {
                "prompt": request.prompt.strip(),
                "images": list(image_urls),
                "sound": (
                    request.audio in {"native", "original"}
                    if request.audio is not None
                    else self.settings.wavespeed_video_sound
                ),
                "aspect_ratio": (
                    request.aspect_ratio or self.settings.wavespeed_video_aspect_ratio
                ),
                "duration": request.duration or self.settings.wavespeed_video_duration,
                "shot_type": "customize" if request.multi_shot is not False else "intelligent",
            }
            submission = await self._client.submit_prediction(
                model,
                payload,
                validate_model=False,
            )
        except WaveSpeedError as exc:
            raise _translate_error(exc) from exc
        return ProviderSubmissionResult(
            provider_task_id=submission.task_id,
            raw_status=submission.status,
            credits_consumed=None,
            provider_model=submission.model_id,
            raw_response=submission.raw_response,
        )

    async def query(self, provider_task_id: str) -> ProviderTaskResult:
        try:
            prediction = await self._client.get_prediction(provider_task_id)
        except WaveSpeedError as exc:
            raise _translate_error(exc) from exc
        status = _provider_status(prediction.status)
        if status == "succeeded" and not prediction.outputs:
            raise WaveSpeedVideoError(
                "WAVESPEED_OUTPUT_URL_MISSING",
                "WaveSpeed task succeeded without a video output URL",
                provider_task_id=provider_task_id,
                raw_status=prediction.status,
            )
        return ProviderTaskResult(
            status=status,
            raw_status=prediction.status,
            outputs=tuple(
                ProviderOutput(url=value, content_type="video") for value in prediction.outputs
            ),
            raw_response=prediction.raw_response,
            message=prediction.error,
        )

    async def download(self, output: ProviderOutput, destination: Path) -> DownloadResult:
        from libtv_agent.adapters.download_worker import stage_download

        existing_size = await asyncio.to_thread(
            lambda: destination.stat().st_size if destination.is_file() else 0
        )
        if existing_size > 0 and await asyncio.to_thread(_is_probably_video_file, destination):
            return DownloadResult(path=destination, reused=True, size_bytes=existing_size)
        temporary = destination.with_suffix(f"{destination.suffix}.part")
        destination.parent.mkdir(parents=True, exist_ok=True)

        def download_file() -> int:
            request = Request(output.url, headers={"User-Agent": "libtv-agent-wavespeed-video/1"})
            with urlopen(
                request,
                timeout=self.settings.wavespeed_video_download_timeout_seconds,
            ) as response:
                status = int(getattr(response, "status", 200))
                if not 200 <= status < 300:
                    raise OSError(f"unexpected HTTP status {status}")
                content_type = str(response.headers.get("Content-Type") or "")
                if content_type and not (
                    content_type.startswith("video/") or content_type == "application/octet-stream"
                ):
                    raise OSError("downloaded WaveSpeed output is not a video")
                with temporary.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                declared_length = response.headers.get("Content-Length")
            size = temporary.stat().st_size
            if declared_length is not None and size != int(declared_length):
                raise OSError("downloaded output length does not match Content-Length")
            if size <= 0:
                raise OSError("downloaded WaveSpeed output is empty")
            return size

        started_at = time.monotonic()
        try:
            size = await stage_download(
                download_file,
                temporary,
                destination,
                timeout=self.settings.wavespeed_video_download_timeout_seconds,
            )
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise WaveSpeedVideoError(
                "WAVESPEED_DOWNLOAD_FAILED",
                "WaveSpeed video output download failed",
                retryable=True,
                details={"errorType": type(exc).__name__},
            ) from exc
        logger.info(
            "WaveSpeed video output downloaded",
            extra={
                "provider": self.get_provider_name(),
                "output_host": urlsplit(output.url).hostname,
                "output_path": str(destination),
                "size_bytes": size,
                "duration_ms": round((time.monotonic() - started_at) * 1000),
            },
        )
        return DownloadResult(path=destination, reused=False, size_bytes=size)

    async def download_media(self, *, url: str, destination: Path) -> Path:
        return (
            await self.download(ProviderOutput(url=url, content_type="video"), destination)
        ).path
