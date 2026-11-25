from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from libtv_agent.adapters.kling_official_api import (
    ApiHttpResponse,
    KlingApiError,
    KlingElementCreateRequest,
    KlingOfficialApiAdapter,
    map_kling_api_status,
)
from libtv_agent.adapters.kling_official_cli import KlingOfficialCliAdapter
from libtv_agent.adapters.kling_provider import (
    create_kling_adapter,
    create_kling_adapter_for_provider,
)
from libtv_agent.adapters.video_provider import VideoElementReference, VideoGenerationRequest
from libtv_agent.config import Settings
from libtv_agent.services.kling_preparation import (
    _asset_suffix,
    _resolve_element_references,
    _resolve_multi_shot,
)
from PIL import Image


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "development",
        "runner_root": tmp_path / "runner",
        "artifact_root": tmp_path / "artifacts",
        "kling_provider_backend": "api",
        "kling_api_enabled": True,
        "kling_api_key": "test-api-key",
        "kling_api_max_retries": 2,
        "kling_api_request_timeout_seconds": 2,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (320, 320), color).save(path)
    return path


def _request(first: Path, second: Path | None = None) -> VideoGenerationRequest:
    return VideoGenerationRequest(
        prompt="原样提示词 @image_2",
        model="kling-4.0",
        image_paths=(first,),
        product_reference_paths=(second,) if second else (),
        duration=10,
        aspect_ratio="9:16",
        resolution="1080p",
        audio="off",
        multi_shot=True,
        external_task_id="local-intent-001",
    )


def test_cli_route_is_selectable_without_changing_api_history(tmp_path: Path) -> None:
    settings = Settings(
        runner_root=tmp_path / "runner",
        kling_provider_backend="cli",
        _env_file=None,
    )

    assert isinstance(create_kling_adapter(settings), KlingOfficialCliAdapter)
    assert isinstance(create_kling_adapter_for_provider(settings, "kling"), KlingOfficialApiAdapter)
    assert isinstance(
        create_kling_adapter_for_provider(settings, settings.kling_cli_provider_name),
        KlingOfficialCliAdapter,
    )
    assert settings.active_kling_provider_name == settings.kling_cli_provider_name
    assert not settings.kling_provider_submission_enabled(settings.kling_cli_provider_name)
    settings.kling_cli_enabled = True
    assert settings.kling_provider_submission_enabled(settings.kling_cli_provider_name)


@pytest.mark.asyncio
async def test_v3_omni_uses_separate_product_reference_and_model_path(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png", "red")
    product = _image(tmp_path / "product.png", "blue")
    adapter = KlingOfficialApiAdapter(_settings(tmp_path, kling_api_model="kling-3.0-omni"))
    request = VideoGenerationRequest(
        prompt="Preserve original product artwork",
        model="kling-3.0-omni",
        image_paths=(first,),
        product_reference_paths=(product,),
        duration=10,
        aspect_ratio="9:16",
        external_task_id="v3-omni-reference-test",
    )
    payload = await adapter.build_request_payload(request)
    assert [(item["type"], item["id"]) for item in payload["contents"][1:]] == [
        ("first_frame", "image_1"), ("refer_image", "image_2"),
    ]
    assert (await adapter.request_summary(request))["endpoint"] == "/omni-video/kling-3.0-omni"
    with pytest.raises(KlingApiError, match="15"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="x", model="kling-3.0-omni", image_paths=(first,), duration=16,
        ))
    with pytest.raises(KlingApiError, match="7 image references"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="x", model="kling-3.0-omni", image_paths=(first,) * 8,
        ))


@pytest.mark.asyncio
async def test_v3_omni_sends_audio_and_multishot_without_rewriting_prompt(tmp_path: Path) -> None:
    image = _image(tmp_path / "first.png", "red")
    adapter = KlingOfficialApiAdapter(_settings(tmp_path, kling_api_model="kling-3.0-omni"))
    request = VideoGenerationRequest(
        prompt="Keep the garment unchanged", model="kling-3.0-omni",
        image_paths=(image,), duration=10, aspect_ratio="9:16",
        resolution="1080p", audio="off", multi_shot=False,
        external_task_id="v3-flags",
    )
    payload = await adapter.build_request_payload(request)
    assert payload["contents"][0]["text"] == request.prompt
    assert payload["settings"] == {
        "resolution": "1080p", "duration": 10, "aspect_ratio": "9:16",
        "audio": "off", "multi_shot": False,
    }
    assert (await adapter.request_summary(request))["settings"] == {
        "resolution": "1080p", "duration": 10, "aspectRatio": "9:16",
        "audio": "off", "multiShot": False,
    }


@pytest.mark.asyncio
async def test_api_history_recovery_requires_exact_id_time_and_credential(tmp_path: Path) -> None:
    created = datetime.now(UTC).replace(microsecond=0)
    calls: list[str] = []

    async def respond(
        method: str, url: str, headers: dict[str, str],
        body: bytes | None, request_timeout: float,
    ) -> ApiHttpResponse:
        del headers, body, request_timeout
        calls.append(method)
        assert "external_task_ids=expected-fingerprint" in url
        return ApiHttpResponse(
            200, {},
            json.dumps({
                "code": 0, "data": [{
                    "id": "remote-verified", "external_id": "expected-fingerprint",
                    "create_time": int(created.timestamp() * 1000),
                    "status": "processing",
                }],
            }).encode(),
        )

    settings = _settings(tmp_path)
    adapter = KlingOfficialApiAdapter(settings, http_request=respond)
    account_id = await adapter.submission_account_id()
    match = await adapter.find_recent_submission(
        account_id=account_id, input_fingerprint="expected-fingerprint",
        submitted_after=created - timedelta(seconds=5),
        submitted_before=created + timedelta(seconds=5),
    )
    assert match is not None
    assert match.provider_task_id == "remote-verified"
    assert match.input_fingerprint == "expected-fingerprint"
    assert match.account_id == account_id
    assert calls == ["GET"]

    assert await adapter.find_recent_submission(
        account_id=account_id, input_fingerprint="expected-fingerprint",
        submitted_after=created + timedelta(minutes=1),
        submitted_before=created + timedelta(minutes=2),
    ) is None

    settings.kling_api_key = "different-api-key"
    with pytest.raises(KlingApiError, match="Original API credential"):
        await adapter.find_recent_submission(
            account_id=account_id, input_fingerprint="expected-fingerprint",
            submitted_after=created - timedelta(seconds=5),
            submitted_before=created + timedelta(seconds=5),
        )
    assert calls == ["GET", "GET"]


@pytest.mark.asyncio
async def test_api_is_unverified_until_official_read_only_query_succeeds(tmp_path: Path) -> None:
    calls: list[tuple[str, str, bytes | None]] = []

    async def verify(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del headers, request_timeout
        calls.append((method, url, body))
        return ApiHttpResponse(
            200,
            {},
            json.dumps({"code": 0, "message": "ok", "data": []}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=verify)

    snapshot = adapter.availability_snapshot()
    assert snapshot.status == "KLING_API_CONFIGURED_UNVERIFIED"
    assert snapshot.available is False
    assert snapshot.authenticated is False

    verified = await adapter.check_availability()
    assert verified.status == "KLING_API_READY"
    assert verified.available is True
    assert verified.authenticated is True
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert "/tasks?external_task_ids=libtv-read-only-provider-verification" in calls[0][1]
    assert calls[0][2] is None

    await adapter.refresh_capabilities()
    await adapter.refresh_capabilities(force=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_read_only_verification_never_reports_ready(tmp_path: Path) -> None:
    async def reject(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del method, url, headers, body, request_timeout
        return ApiHttpResponse(
            401,
            {},
            json.dumps({"code": 1001, "message": "invalid credential"}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=reject)
    availability = await adapter.check_availability()

    assert availability.status == "KLING_API_AUTHENTICATION_FAILED"
    assert availability.available is False
    assert availability.authenticated is False


@pytest.mark.asyncio
async def test_kling_4_http_contract_uses_bearer_and_valid_schema(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def respond(
        method: str, url: str, headers: dict[str, str],
        body: bytes | None, request_timeout: float,
    ) -> ApiHttpResponse:
        del request_timeout
        calls.append({
            "method": method, "url": url, "headers": headers,
            "payload": json.loads(body) if body else None,
        })
        return ApiHttpResponse(
            200, {},
            json.dumps({"code": 0, "message": "ok", "data": {
                "id": "kling-task-accepted", "status": "submitted",
            }}).encode(),
        )

    settings = _settings(
        tmp_path, kling_api_base_url="https://api-singapore.klingai.com",
    )
    adapter = KlingOfficialApiAdapter(settings, http_request=respond)
    result = await adapter.submit(VideoGenerationRequest(
        prompt="A faithful 9:16 product showcase",
        model="kling-4.0", duration=15, aspect_ratio="9:16",
        resolution="1080p", audio="off", multi_shot=True,
        external_task_id="contract-check",
    ))
    assert result.provider_task_id == "kling-task-accepted"
    assert len(calls) == 1
    sent = calls[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "https://api-singapore.klingai.com/omni-video/kling-4.0"
    assert sent["headers"]["Authorization"] == "Bearer test-api-key"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["payload"] == {
        "contents": [{"type": "prompt", "text": "A faithful 9:16 product showcase"}],
        "settings": {
            "resolution": "1080p", "duration": 15, "aspect_ratio": "9:16",
        },
        "options": {
            "external_task_id": "contract-check",
            "watermark_info": {"enabled": False},
        },
    }


@pytest.mark.asyncio
async def test_request_mapping_preserves_prompt_and_image_order(tmp_path: Path) -> None:
    first = _image(tmp_path / "01-first.png", "red")
    second = _image(tmp_path / "02-product.jpg", "blue")
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))

    payload = await adapter.build_request_payload(_request(first, second))

    assert payload["contents"][0] == {"type": "prompt", "text": "原样提示词 @image_2"}
    assert [(item["type"], item["id"]) for item in payload["contents"][1:]] == [
        ("first_frame", "image_1"),
        ("refer_image", "image_2"),
    ]
    assert base64.b64decode(payload["contents"][1]["url"]) == first.read_bytes()
    assert base64.b64decode(payload["contents"][2]["url"]) == second.read_bytes()
    assert payload["settings"] == {
        "resolution": "1080p",
        "duration": 10,
        "aspect_ratio": "9:16",
    }
    assert payload["options"]["external_task_id"] == "local-intent-001"
    summary = await adapter.request_summary(_request(first, second))
    assert summary["endpoint"] == "/omni-video/kling-4.0"
    assert summary["model"] == "kling-4.0"
    assert summary["settings"] == {
        "resolution": "1080p",
        "duration": 10,
        "aspectRatio": "9:16",
    }


@pytest.mark.asyncio
async def test_kling_4_omits_legacy_audio_and_multishot_on_15s_job(tmp_path: Path) -> None:
    # Regression for an actual pre-submit 400/1201: audio='off' rejected by V4.
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    request = VideoGenerationRequest(
        prompt="Follow the original storyboard with clear shot changes.",
        model="kling-4.0",
        duration=15,
        aspect_ratio="9:16",
        resolution="1080p",
        audio="off",
        multi_shot=True,
        external_task_id="regression-audio-400",
    )
    payload = await adapter.build_request_payload(request)
    assert payload["settings"] == {
        "resolution": "1080p",
        "duration": 15,
        "aspect_ratio": "9:16",
    }
    assert payload["contents"] == [{"type": "prompt", "text": request.prompt}]
    assert (await adapter.request_summary(request))["settings"] == {
        "resolution": "1080p",
        "duration": 15,
        "aspectRatio": "9:16",
    }


@pytest.mark.asyncio
async def test_kling_4_reference_limits_match_official_2026_release(tmp_path: Path) -> None:
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    image = _image(tmp_path / "reference.png", "blue")
    valid = VideoGenerationRequest(
        prompt="x" * 26_000, model="kling-4.0",
        image_paths=(image,) * 10,
        external_task_id="contract-limit-test",
    )
    payload = await adapter.build_request_payload(valid)
    assert len(payload["contents"]) == 11

    with pytest.raises(KlingApiError, match="26000 characters"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="x" * 26_001, model="kling-4.0",
        ))
    with pytest.raises(KlingApiError, match="10 image references"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="Reference", model="kling-4.0", image_paths=(image,) * 11,
        ))
    with pytest.raises(KlingApiError, match="7 element references"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="Reference", model="kling-4.0",
            element_references=tuple(
                VideoElementReference(str(i), f"element_{i}") for i in range(8)
            ),
        ))
    with pytest.raises(KlingApiError, match="15 combined references"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="Reference", model="kling-4.0", image_paths=(image,) * 10,
            element_references=tuple(
                VideoElementReference(str(i), f"element_{i}") for i in range(6)
            ),
        ))
    with pytest.raises(KlingApiError, match="base_video"):
        await adapter.validate_request(VideoGenerationRequest(
            prompt="Reference", model="kling-4.0",
            base_video_url="https://example.com/clip.mp4",
        ))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duration", "accepted"),
    [(2, False), (3, True), (16, True), (30, True), (31, False)],
)
async def test_kling_4_duration_boundaries(
    tmp_path: Path, duration: int, accepted: bool
) -> None:
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    request = VideoGenerationRequest(
        prompt="A quiet sunrise",
        model="kling-4.0",
        duration=duration,
        external_task_id="duration-boundary",
    )
    if accepted:
        payload = await adapter.build_request_payload(request)
        assert payload["settings"]["duration"] == duration
    else:
        with pytest.raises(KlingApiError, match="3 and 30 seconds"):
            await adapter.build_request_payload(request)


@pytest.mark.asyncio
async def test_prompt_only_and_explicit_omni_content_mapping(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png", "red")
    last = _image(tmp_path / "last.png", "blue")
    refer = _image(tmp_path / "refer.png", "green")
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))

    prompt_only = await adapter.build_request_payload(
        VideoGenerationRequest(
            prompt="A quiet sunrise",
            model="kling-4.0",
            external_task_id="prompt-only",
        )
    )
    assert prompt_only["contents"] == [{"type": "prompt", "text": "A quiet sunrise"}]

    payload = await adapter.build_request_payload(
        VideoGenerationRequest(
            prompt="Use every explicit reference",
            model="kling-4.0",
            first_frame_path=first,
            last_frame_path=last,
            refer_image_paths=(refer,),
            feature_video_url="https://cdn.example/feature.mp4",
            element_references=(VideoElementReference("172", "element_1"),),
            external_task_id="explicit-inputs",
        )
    )
    assert [(item["type"], item.get("id")) for item in payload["contents"][1:]] == [
        ("first_frame", "image_1"),
        ("last_frame", "image_2"),
        ("refer_image", "image_3"),
        ("feature_video", "video_1"),
        ("element", "element_1"),
    ]
    assert payload["contents"][-1]["element_id"] == "172"


@pytest.mark.asyncio
async def test_last_frame_without_first_frame_is_rejected(tmp_path: Path) -> None:
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    with pytest.raises(KlingApiError, match="last frame"):
        await adapter.validate_request(
            VideoGenerationRequest(
                prompt="invalid",
                model="kling-4.0",
                last_frame_path=_image(tmp_path / "last.png", "blue"),
            )
        )


def test_normalized_object_suffix_wins_over_original_webp_name() -> None:
    assert _asset_suffix("source.webp", "users/u/inputs/a/normalized.jpg") == ".jpg"


def test_variant_multi_shot_overrides_smart_storyboard_and_global(tmp_path: Path) -> None:
    settings = _settings(tmp_path, kling_api_multi_shot=True)
    assert _resolve_multi_shot({"multiShot": False, "smartStoryboard": True}, settings) is False
    assert _resolve_multi_shot({"smartStoryboard": False}, settings) is False
    assert _resolve_multi_shot({}, settings) is True


def test_element_ids_become_stable_prompt_references() -> None:
    references = _resolve_element_references({"elementIds": ["172", "173"]})
    assert references == (
        VideoElementReference("172", "element_1"),
        VideoElementReference("173", "element_2"),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("submitted", "queued"),
        ("processing", "running"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("success", "unknown"),
        ("completed", "unknown"),
    ],
)
def test_status_mapping_is_strict(raw: str, expected: str) -> None:
    assert map_kling_api_status(raw) == expected


def test_task_result_preserves_failure_message_watermark_and_all_billing(tmp_path: Path) -> None:
    adapter = KlingOfficialApiAdapter(_settings(tmp_path))
    result = adapter._task_result(
        {
            "id": "task-1",
            "status": "failed",
            "message": "content policy rejected the request",
            "outputs": [
                {
                    "type": "video",
                    "url": "https://cdn.example/clean.mp4",
                    "watermark_url": "https://cdn.example/watermarked.mp4",
                }
            ],
            "billing": [
                {"charge_type": "unit", "amount": "1.5", "package_type": "video"},
                {"charge_type": "cash", "amount": "0.25", "list_price": "0.50"},
            ],
        }
    )

    assert result.message == "content policy rejected the request"
    assert result.outputs[0].url_without_watermark == "https://cdn.example/clean.mp4"
    assert result.outputs[0].watermark_url == "https://cdn.example/watermarked.mp4"
    assert result.credits_consumed is None
    assert [charge.amount for charge in result.billing] == [
        Decimal("1.5"),
        Decimal("0.25"),
    ]
    assert result.billing[1].list_price == Decimal("0.50")


@pytest.mark.asyncio
async def test_batch_and_cursor_task_queries_use_official_contract(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def respond(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del headers, request_timeout
        payload = json.loads(body) if body else None
        calls.append((method, url, payload))
        data: Any
        if method == "GET":
            data = [
                {"id": "task-1", "status": "processing", "outputs": []},
                {"id": "task-2", "status": "failed", "message": "rejected", "outputs": []},
            ]
        else:
            data = {
                "result": [{"id": "task-3", "status": "processing", "outputs": []}],
                "count": 1,
                "next_cursor": "next-1",
                "has_more": True,
            }
        return ApiHttpResponse(
            200,
            {},
            json.dumps({"code": 0, "message": "ok", "data": data}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=respond)
    batch = await adapter.query_many(task_ids=("task-1", "task-2"))
    page = await adapter.query_tasks_cursor(
        cursor="cursor-1",
        limit=25,
        filters=({"key": "status", "values": ["processing"]},),
    )

    assert [task.raw_status for task in batch] == ["processing", "failed"]
    assert "task_ids=task-1%2Ctask-2" in calls[0][1]
    assert calls[1] == (
        "POST",
        f"{adapter.settings.kling_api_base_url}/tasks",
        {
            "limit": 25,
            "cursor": "cursor-1",
            "filters": [{"key": "status", "values": ["processing"]}],
        },
    )
    assert page.next_cursor == "next-1"
    assert page.has_more is True


@pytest.mark.asyncio
async def test_element_rest_client_maps_create_query_list_and_delete(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def respond(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del headers, request_timeout
        calls.append((method, url, json.loads(body) if body else None))
        return ApiHttpResponse(
            200,
            {},
            json.dumps({"code": 0, "message": "ok", "data": {}}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=respond)
    await adapter.create_element(
        KlingElementCreateRequest(
            element_name="Actor",
            element_description="Primary actor",
            reference_type="image_refer",
            frontal_image="https://cdn.example/front.jpg",
            refer_images=("https://cdn.example/side.jpg",),
            element_voice_id="voice-1",
            tag_ids=("o_102",),
            external_task_id="element-task-1",
        )
    )
    await adapter.get_element("element-task-1")
    await adapter.list_elements(cursor="2", limit=10)
    await adapter.delete_element("element-1")

    assert calls[0][0:2] == (
        "POST",
        f"{adapter.settings.kling_api_base_url}/v1/general/advanced-custom-elements",
    )
    assert calls[0][2]["element_voice_id"] == "voice-1"
    assert calls[0][2]["element_image_list"]["refer_images"] == [
        {"image_url": "https://cdn.example/side.jpg"}
    ]
    assert calls[1][1].endswith("/advanced-custom-elements/element-task-1")
    assert calls[2][1].endswith("/advanced-custom-elements?pageNum=2&pageSize=10")
    assert calls[3][2] == {"element_id": "element-1"}


@pytest.mark.asyncio
async def test_rate_limit_is_retryable_and_honors_retry_after(tmp_path: Path) -> None:
    async def limited(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del method, url, headers, body, request_timeout
        return ApiHttpResponse(
            429,
            {"Retry-After": "7"},
            json.dumps({"code": 1303, "message": "concurrency limited"}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=limited)
    with pytest.raises(KlingApiError) as caught:
        await adapter.query("task-1")
    assert caught.value.code == "KLING_API_RATE_LIMITED"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 7
    assert caught.value.uncertain is False


@pytest.mark.asyncio
@pytest.mark.parametrize("service_code", [1100, 1101, 1102, 1103])
async def test_rejected_account_submission_is_not_unknown(
    tmp_path: Path, service_code: int,
) -> None:
    calls = []

    async def reject(method, url, headers, body, request_timeout):
        calls.append(method)
        return ApiHttpResponse(
            200, {}, json.dumps({"code": service_code, "message": "Account unavailable"}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=reject)
    with pytest.raises(KlingApiError) as caught:
        await adapter.submit(_request(_image(tmp_path / "first.png", "red")))
    assert caught.value.code == "KLING_API_ACCOUNT_UNAVAILABLE"
    assert caught.value.submitted is False
    assert caught.value.uncertain is False
    assert calls == ["POST"]


@pytest.mark.asyncio
async def test_explicit_balance_shortage_has_own_error_code(tmp_path: Path) -> None:
    async def reject(method, url, headers, body, request_timeout):
        return ApiHttpResponse(
            200, {}, json.dumps({"code": 1102, "message": "Account balance not enough"}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=reject)
    with pytest.raises(KlingApiError) as caught:
        await adapter.submit(_request(_image(tmp_path / "first.png", "red")))
    assert caught.value.code == "KLING_API_BALANCE_INSUFFICIENT"
    assert caught.value.submitted is False
    assert caught.value.uncertain is False


@pytest.mark.asyncio
async def test_ip_allowlist_error_is_not_retried(tmp_path: Path) -> None:
    calls = []

    async def reject(method, url, headers, body, request_timeout):
        calls.append(method)
        return ApiHttpResponse(
            429, {}, json.dumps({"code": 1304, "message": "IP restricted"}).encode()
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=reject)
    with pytest.raises(KlingApiError) as caught:
        await adapter.submit(_request(_image(tmp_path / "first.png", "red")))
    assert caught.value.code == "KLING_API_VALIDATION_FAILED"
    assert caught.value.retryable is False
    assert calls == ["POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_code", [0, 1001, 1303])
async def test_uncertain_submit_never_reposts_after_unresolved_lookup(
    tmp_path: Path, lookup_code: int
) -> None:
    calls = []

    async def unresolved(method, url, headers, body, request_timeout):
        calls.append(method)
        if method == "POST":
            raise TimeoutError
        return ApiHttpResponse(
            {0: 200, 1001: 401, 1303: 429}[lookup_code],
            {},
            json.dumps({"code": lookup_code, "data": []}).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=unresolved)
    with pytest.raises(KlingApiError) as caught:
        await adapter.submit(_request(_image(tmp_path / "first.png", "red")))
    assert caught.value.code == "KLING_API_NETWORK_ERROR"
    assert caught.value.uncertain is True
    assert caught.value.submitted is True
    assert calls == ["POST", "GET"]


@pytest.mark.asyncio
async def test_timeout_recovers_by_external_task_id_without_second_submit(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png", "red")
    calls: list[tuple[str, str]] = []

    async def recover(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del headers, body, request_timeout
        calls.append((method, url))
        if method == "POST":
            raise TimeoutError
        return ApiHttpResponse(
            200,
            {},
            json.dumps(
                {
                    "code": 0,
                    "message": "ok",
                    "data": [
                        {
                            "id": "remote-1",
                            "external_id": "local-intent-001",
                            "status": "processing",
                            "outputs": [],
                        }
                    ],
                }
            ).encode(),
        )

    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=recover)
    result = await adapter.submit(_request(first))

    assert result.provider_task_id == "remote-1"
    assert result.raw_response["recoveredByExternalTaskId"] is True
    assert [method for method, _ in calls].count("POST") == 1
    assert "external_task_ids=local-intent-001" in calls[-1][1]


class _KlingMockHandler(BaseHTTPRequestHandler):
    post_bodies: list[dict[str, Any]] = []
    query_count = 0
    video = b"\x00\x00\x00\x18ftypmp42mock-video"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        self.post_bodies.append(json.loads(self.rfile.read(length)))
        self._json(
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "id": "mock-task",
                    "external_id": "local-intent-001",
                    "status": "submitted",
                },
            }
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/video.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(self.video)))
            self.end_headers()
            self.wfile.write(self.video)
            return
        self.__class__.query_count += 1
        status = "processing" if self.query_count < 3 else "succeeded"
        outputs = (
            []
            if status == "processing"
            else [
                {
                    "type": "video",
                    "id": "video-1",
                    "url": f"http://127.0.0.1:{self.server.server_port}/video.mp4",
                    "duration": 10,
                }
            ]
        )
        self._json(
            {
                "code": 0,
                "message": "ok",
                "data": [
                    {
                        "id": "mock-task",
                        "external_id": "local-intent-001",
                        "status": status,
                        "outputs": outputs,
                        "billing": [{"charge_type": "unit", "amount": 8}],
                    }
                ],
            }
        )


@contextmanager
def _mock_server() -> Iterator[ThreadingHTTPServer]:
    import os

    _KlingMockHandler.post_bodies = []
    _KlingMockHandler.query_count = 0
    port = int(os.environ.get("G1_TEST_HTTP_PORT", "0"))
    server = ThreadingHTTPServer(("127.0.0.1", port), _KlingMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_mock_http_submit_poll_and_atomic_download(tmp_path: Path) -> None:
    first = _image(tmp_path / "first.png", "green")
    with _mock_server() as server:
        adapter = KlingOfficialApiAdapter(
            _settings(tmp_path, kling_api_base_url=f"http://127.0.0.1:{server.server_port}")
        )
        submission = await adapter.submit(_request(first))
        statuses = []
        for _ in range(3):
            result = await adapter.query(submission.provider_task_id)
            statuses.append(result.status)
        destination = tmp_path / "output" / "video.mp4"
        downloaded = await adapter.download(result.outputs[0], destination)

    assert statuses == ["running", "running", "succeeded"]
    assert result.credits_consumed == 8
    assert downloaded.path.read_bytes() == _KlingMockHandler.video
    assert not destination.with_suffix(".mp4.part").exists()
    assert len(_KlingMockHandler.post_bodies) == 1
    assert _KlingMockHandler.post_bodies[0]["contents"][0]["text"] == "原样提示词 @image_2"


@pytest.mark.asyncio
async def test_download_replaces_non_video_destination_instead_of_reusing_it(
    tmp_path: Path,
) -> None:
    with _mock_server() as server:
        adapter = KlingOfficialApiAdapter(
            _settings(tmp_path, kling_api_base_url=f"http://127.0.0.1:{server.server_port}")
        )
        destination = tmp_path / "output.mp4"
        destination.write_bytes(b"not-a-video")
        result = await adapter.download(
            adapter._task_result(
                {
                    "id": "task",
                    "status": "succeeded",
                    "outputs": [
                        {
                            "type": "video",
                            "url": f"http://127.0.0.1:{server.server_port}/video.mp4",
                        }
                    ],
                }
            ).outputs[0],
            destination,
        )

    assert result.reused is False
    assert destination.read_bytes() == _KlingMockHandler.video


@pytest.mark.asyncio
async def test_submit_retries_429_without_rewriting_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _image(tmp_path / "first.png", "white")
    bodies: list[bytes] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def flaky(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> ApiHttpResponse:
        del method, url, headers, request_timeout
        bodies.append(body or b"")
        if len(bodies) == 1:
            return ApiHttpResponse(
                429,
                {"Retry-After": "2"},
                json.dumps({"code": 1302, "message": "rate limited"}).encode(),
            )
        return ApiHttpResponse(
            200,
            {},
            json.dumps(
                {
                    "code": 0,
                    "message": "ok",
                    "data": {"id": "task-2", "status": "submitted"},
                }
            ).encode(),
        )

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    adapter = KlingOfficialApiAdapter(_settings(tmp_path), http_request=flaky)
    result = await adapter.submit(_request(first))

    assert result.provider_task_id == "task-2"
    assert bodies[0] == bodies[1]
    assert sleeps and sleeps[0] >= 2
