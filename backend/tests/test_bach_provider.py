import base64
import json
from pathlib import Path

import pytest
from libtv_agent.adapters.bach_provider import (
    BachApiAdapter,
    BachApiError,
    BachHttpResponse,
    build_bach_token,
)
from libtv_agent.adapters.video_provider import (
    DownloadResult,
    ProviderOutput,
    VideoGenerationRequest,
)
from libtv_agent.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        bach_enabled=True,
        bach_access_key="bach-access-key",
        bach_secret_key="bach-secret-key",
        bach_api_base_url="http://127.0.0.1:9999/api/vdr",
        bach_poll_interval_seconds=1,
        runner_root=tmp_path,
        _env_file=None,
    )


def _decode_segment(value: str) -> dict[str, object]:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_bach_token_has_required_claims_without_exposing_secret() -> None:
    token = build_bach_token("access-key", "secret-key", now=1_800_000_000)
    header, payload, _signature = token.split(".")

    assert _decode_segment(header) == {"alg": "HS256", "typ": "JWT"}
    assert _decode_segment(payload) == {
        "iss": "access-key",
        "nbf": 1_799_999_995,
        "exp": 1_800_864_000,
    }
    assert "secret-key" not in token


@pytest.mark.asyncio
async def test_bach_image_to_video_upload_submit_and_query(tmp_path: Path) -> None:
    image = tmp_path / "first.jpg"
    image.write_bytes(b"test-image")
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    async def fake_http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> BachHttpResponse:
        del request_timeout
        calls.append((method, url, headers, body))
        if url.endswith("/batch-upload/submit-with-files"):
            return BachHttpResponse(
                200,
                {},
                b'{"code":200,"data":{"batch_id":"batch-1","status":"TASK_PENDING"}}',
            )
        if url.endswith("/batch-upload/status/batch-1"):
            return BachHttpResponse(
                200,
                {},
                b'{"code":200,"data":{"status":"TASK_SUCCEEDED","results":'
                b'[{"index":0,"status":"TASK_SUCCEEDED","image_url":"https://cdn/a.jpg"}]}}',
            )
        if method == "POST" and url.endswith("/videos/image2video"):
            return BachHttpResponse(
                200,
                {},
                b'{"code":200,"data":{"task_id":"task-1","status":"TASK_PENDING"}}',
            )
        return BachHttpResponse(
            200,
            {},
            b'{"code":200,"data":{"task_id":"task-1","status":"TASK_SUCCEEDED",'
            b'"video_url":"https://cdn/result.mp4"}}',
        )

    adapter = BachApiAdapter(_settings(tmp_path), http_request=fake_http)
    submission = await adapter.submit(
        VideoGenerationRequest(
            prompt="A model turns to show the garment",
            model="bach-1.0",
            image_paths=(image,),
            duration=6,
            resolution="720p",
            external_task_id="intent-1",
            task_trace_id="trace-1",
        )
    )
    result = await adapter.query(submission.provider_task_id)

    assert submission.provider_task_id == "task-1"
    assert result.status == "succeeded"
    assert result.outputs[0].url == "https://cdn/result.mp4"
    assert all(call[2]["Authorization"].startswith("Bearer ") for call in calls)
    task_payload = json.loads(next(call[3] for call in calls if call[1].endswith("image2video")))
    assert task_payload["image_url"] == "https://cdn/a.jpg"
    assert task_payload["duration"] == 6


@pytest.mark.asyncio
async def test_bach_rejects_http_200_business_error(tmp_path: Path) -> None:
    async def fake_http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        request_timeout: float,
    ) -> BachHttpResponse:
        del method, url, headers, body, request_timeout
        return BachHttpResponse(200, {}, b'{"code":1302,"message":"Insufficient credits"}')

    adapter = BachApiAdapter(_settings(tmp_path), http_request=fake_http)
    with pytest.raises(BachApiError) as caught:
        await adapter._request_json("GET", "/videos/image2video/task-1")

    assert caught.value.code == "BACH_CODE_1302"
    assert caught.value.retryable is False


@pytest.mark.parametrize(("http_status", "code", "ambiguous"), [
    (200, 1302, False),  # Insufficient credits is a rejected submission.
    (402, 1302, False),
    (200, 1001, False),
    (200, 5000, True),
    (503, 1302, True),  # A server failure is still ambiguous.
    (200, 9999, True),  # Unrecognized business errors stay conservative.
])
def test_bach_submission_rejection_is_distinct_from_unknown(
    http_status: int, code: int, ambiguous: bool,
) -> None:
    response = BachHttpResponse(
        http_status, {}, json.dumps({"code": code, "message": "test failure"}).encode(),
    )
    with pytest.raises(BachApiError) as caught:
        BachApiAdapter._decode_response(response, submitted=True)
    assert caught.value.submitted is ambiguous
    assert caught.value.uncertain is ambiguous


@pytest.mark.asyncio
async def test_bach_download_media_uses_provider_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = BachApiAdapter(_settings(tmp_path))
    destination = tmp_path / "result.mp4"
    calls: list[tuple[ProviderOutput, Path]] = []

    async def fake_download(output: ProviderOutput, path: Path) -> DownloadResult:
        calls.append((output, path))
        return DownloadResult(path=path, reused=False, size_bytes=123)

    monkeypatch.setattr(adapter, "download", fake_download)

    result = await adapter.download_media(
        url="https://cdn.example/result.mp4", destination=destination
    )

    assert result == destination
    assert calls == [
        (ProviderOutput(url="https://cdn.example/result.mp4", content_type="video"), destination)
    ]
