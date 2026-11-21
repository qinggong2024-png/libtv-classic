"""Offline-only tests for safe upstream error tracing in the Classic probe."""
from __future__ import annotations

import json

import httpx
from openai import BadRequestError

from scripts.probe_classic_multimodal_api import safe_error


def test_gateway_request_id_is_reported_without_leaking_upstream_body():
    private = "sk-SUPER-PRIVATE-DO-NOT-PRINT"
    request = httpx.Request(
        "POST", "https://example.invalid/v1/responses",
        headers={"authorization": f"Bearer {private}"},
    )
    response = httpx.Response(
        400, request=request,
        headers={
            "x-request-id": "req_5sec_abc123",
            "cf-ray": "edge-ABC123",
            "set-cookie": f"token={private}",
        },
        json={"error": {
            "type": "invalid_request_error",
            "code": None,
            "param": None,
            "message": f"Request rejected; image data and token {private}",
        }},
    )
    err = BadRequestError(
        "Bad Request", response=response, body=response.json()
    )
    result = safe_error(err)
    assert result["httpStatus"] == 400
    assert result["providerErrorType"] == "invalid_request_error"
    assert result["gatewayTraceIds"]["x-request-id"] == "req_5sec_abc123"
    assert result["gatewayTraceIds"]["cf-ray"] == "edge-ABC123"
    assert result["sdkRequestId"] == "req_5sec_abc123"
    assert len(result["providerMessageFingerprint"]) == 16
    assert "message" in result["providerErrorFields"]
    assert private not in json.dumps(result, ensure_ascii=False)
    assert "set-cookie" not in json.dumps(result).lower()
    assert "Request rejected" not in json.dumps(result)


def test_network_timeout_has_no_fabricated_gateway_trace():
    err = httpx.ConnectTimeout("connect timed out")
    result = safe_error(err)
    assert result["httpStatus"] is None
    assert result["gatewayTraceIds"] == {}
    assert result["sdkRequestId"] is None
    assert result["providerMessageFingerprint"] is None


def test_malicious_trace_identifier_is_not_exposed():
    request = httpx.Request("POST", "https://example.invalid/v1/responses")
    response = httpx.Response(
        400, request=request,
        headers={
            "x-request-id": "Bearer sensitive-header-content",
            "x-correlation-id": "sk-private-not-a-request-id",
        },
        json={"error": {"type": "invalid_request_error", "message": "bad request"}},
    )
    err = BadRequestError("Bad Request", response=response, body=response.json())
    assert safe_error(err)["gatewayTraceIds"] == {}
