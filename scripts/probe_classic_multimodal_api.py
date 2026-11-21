"""Read-only live multimodal API compatibility probe for Classic.

Real AI requests incur provider usage; NEVER creates a task or calls a video provider.
The administrator API key is loaded in memory from the existing encrypted credentials
and is never printed, copied, or persisted.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = Path(r"E:\Projects\libtv-classic-0e3d1c8-main")
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(DELIVERY / "scripts"))

import classic_lan
from openai import AsyncOpenAI
from pydantic import BaseModel
import httpx

from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import plan_creative_video, planner_timeout_seconds
from libtv_agent.services.credentials import load_admin_credentials
from libtv_agent.services.fixed_video_tasks import _reference_role_prompt
from libtv_agent.templates.garment_materials import garment_video_material_hint

SAMPLE_ROOT = DELIVERY / "var/classic-lan/artifacts/classic-lan-0e3d1c8/users/012f2296-2ecb-469b-a4bc-dcc172515cac/inputs"
STORYBOARD = SAMPLE_ROOT / "383d05af-8df4-4796-b156-65c3cc2ef3b8/normalized.jpg"
PRODUCT = SAMPLE_ROOT / "cab9c0e5-e84a-46b8-a537-c477a2d4259b/normalized.jpg"


class TextProbe(BaseModel):
    ok: str


def safe_error(error: BaseException) -> dict:
    """Only coarse class/status; never include request, response body, or credentials."""
    root = error
    while root.__cause__ is not None:
        root = root.__cause__
    details = getattr(root, "body", None)
    if isinstance(details, dict):
        details = details.get("error", details)
    if not isinstance(details, dict):
        details = {}
    # Only expose allowlisted, short diagnostic identifiers, not error prose:
    # a proxy error message can echo a private prompt, key, or image payload.
    detail_text = str(details.get("message", "")).casefold()
    schema_markers = (
        "schema", "default", "required", "strict", "unsupported", "field",
        "model", "param", "image", "invalid", "proxy", "rate", "duration",
    )

    def safe_identifier(value: object) -> str | None:
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:/-]{1,128}", value
        ):
            return None
        if value.casefold().startswith(("sk-", "bearer", "sess-", "eyj")):
            return None
        return value

    response = getattr(root, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    trace_ids = {
        name: identifier
        for name in (
            "x-request-id", "x-correlation-id", "x-trace-id", "cf-ray",
            "x-oneapi-request-id", "x-gateway-request-id",
        )
        if (identifier := safe_identifier(headers.get(name))) is not None
    }
    request_id = safe_identifier(getattr(root, "request_id", None))
    return {
        "exception": type(error).__name__,
        "rootException": type(root).__name__,
        "httpStatus": getattr(root, "status_code", None),
        "providerErrorCode": safe_identifier(getattr(root, "code", None)),
        "providerErrorType": safe_identifier(details.get("type")),
        "providerErrorParam": safe_identifier(details.get("param")),
        "providerErrorFields": sorted(
            key for key in details if re.fullmatch(r"[A-Za-z_]{1,35}", key)
        ),
        "schemaDiagnosticMarkers": [x for x in schema_markers if x in detail_text],
        "providerMessageFingerprint": (
            hashlib.sha256(detail_text.encode("utf-8")).hexdigest()[:16]
            if detail_text else None
        ),
        "gatewayTraceIds": trace_ids,
        "sdkRequestId": request_id,
    }


def image_input(path: Path, role: str) -> AnalysisImageInput:
    return AnalysisImageInput(
        asset_id=path.parent.name, order=1, role=role,
        image_url="data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
    )


class CapturingResponses:
    def __init__(self, client: AsyncOpenAI):
        self.client = client
        self.calls = 0
        self.elapsed_seconds = None
        self.usage = None
        self.model = None
        self.parsed = None

    async def parse(self, **kwargs):
        self.calls += 1
        started = time.monotonic()
        response = await self.client.responses.parse(**kwargs)
        self.elapsed_seconds = round(time.monotonic() - started, 2)
        self.usage = response.usage.model_dump() if response.usage else None
        self.model = response.model
        self.parsed = response.output_parsed
        return response


class CapturingClient:
    def __init__(self, client: AsyncOpenAI):
        self.responses = CapturingResponses(client)


async def probe(phase: str, model_override: str | None = None, material: str = "unknown",
                duration_seconds: int = 10, non_ribbed: bool = False):
    env = classic_lan.lan_environment(8002, 5176, "192.168.30.126")
    settings = Settings(
        _env_file=None,
        admin_credentials_file=env["ADMIN_CREDENTIALS_FILE"],
        openai_model=model_override or env.get("OPENAI_MODEL", "gpt-5.6-luna"),
        ai_adapter_mode="openai",  # probe explicitly opts into real AI, no video calls
    )
    load_admin_credentials(settings)
    print({"effectiveLanMode": env["AI_ADAPTER_MODE"], "testMode": settings.ai_adapter_mode,
           "keyLoaded": bool(settings.openai_api_key), "baseUrl": settings.openai_base_url,
           "model": settings.openai_model, "material": material,
           "durationSeconds": duration_seconds, "nonRibbed": non_ribbed}, flush=True)
    if not settings.openai_api_key:
        raise RuntimeError("encrypted administrator API key unavailable")
    probe_timeout = (
        httpx.Timeout(connect=12.0, read=planner_timeout_seconds(settings, 2) - 8.0,
                      write=40.0, pool=12.0)
        if phase in ("visual", "all") else 65.0
    )
    async with AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                           timeout=probe_timeout, max_retries=0) as client:
        if phase == "image-wire":
            try:
                response = await client.responses.create(
                    model=settings.openai_model,
                    max_output_tokens=64,
                    input=[{"role": "user", "content": [
                        {"type": "input_text", "text": "Describe the photographed garment briefly."},
                        {"type": "input_image", "image_url": image_input(PRODUCT, "PRODUCT").image_url,
                         "detail": "high"},
                    ]}],
                )
                print({"probe": "image-wire", "passed": True, "model": response.model})
            except Exception as exc:
                print({"probe": "image-wire", "passed": False, **safe_error(exc)})
            return
        if phase == "schema":
            try:
                from libtv_agent.services.ai_creative_planner import CreativeDirectorResponse
                resp = await client.responses.parse(
                    model=settings.openai_model,
                    input=[{"role": "user", "content":
                            "Schema transport compatibility probe; return any valid schema result."}],
                    text_format=CreativeDirectorResponse,
                    max_output_tokens=64,
                )
                print({"probe": "schema", "httpStatus": 200,
                       "parsed": resp.output_parsed is not None})
            except Exception as exc:
                print({"probe": "schema", **safe_error(exc)})
            return
        if phase == "wire":
            specs = [
                ("responses", settings.openai_model, {"model": settings.openai_model,
                 "input": "Answer with the word ready.", "max_output_tokens": 120}),
                ("chat/completions", settings.openai_model, {"model": settings.openai_model,
                 "messages": [{"role": "user", "content": "Answer with the word ready."}],
                 "max_completion_tokens": 120}),
                ("responses", "gpt-5.4-mini", {"model": "gpt-5.4-mini",
                 "input": "Answer with the word ready.", "max_output_tokens": 120}),
            ]
            async with httpx.AsyncClient(timeout=25.0) as raw_client:
                for route, model, payload in specs:
                    started = time.monotonic()
                    try:
                        response = await raw_client.post(
                            settings.openai_base_url.rstrip("/") + "/" + route,
                            headers={"Authorization": "Bearer " + settings.openai_api_key},
                            json=payload,
                        )
                        error = response.json().get("error", {}) if not response.is_success else {}
                        if not isinstance(error, dict):
                            error = {}
                        print({"probe": route, "model": model,
                               "httpStatus": response.status_code,
                               "elapsedSeconds": round(time.monotonic() - started, 2),
                               "errorType": error.get("type"),
                               "errorCode": error.get("code")}, flush=True)
                    except Exception as exc:
                        print({"probe": route, "model": model,
                               "elapsedSeconds": round(time.monotonic() - started, 2),
                               **safe_error(exc)}, flush=True)
            return
        if phase in ("text", "all"):
            started = time.monotonic()
            try:
                answer = await client.responses.parse(
                    model=settings.openai_model,
                    reasoning={"effort": settings.openai_reasoning_effort},
                    max_output_tokens=140,
                    input=[{"role": "user", "content": "Return JSON with ok set to ready."}],
                    text_format=TextProbe,
                )
                print({"probe": "responses.parse.text", "passed": answer.output_parsed is not None,
                       "value": answer.output_parsed.ok if answer.output_parsed else None,
                       "elapsedSeconds": round(time.monotonic() - started, 2),
                       "usage": answer.usage.model_dump() if answer.usage else None}, flush=True)
            except Exception as exc:
                print({"probe": "responses.parse.text", "passed": False,
                       "elapsedSeconds": round(time.monotonic() - started, 2),
                       **safe_error(exc)}, flush=True)
                if phase == "text":
                    return
        if phase in ("visual", "all"):
            if not STORYBOARD.is_file() or not PRODUCT.is_file():
                print({"probe": "multimodal.planner", "passed": False, "error": "images missing"})
                return
            tracking = CapturingClient(client)
            started = time.monotonic()
            try:
                results = await plan_creative_video(
                    settings=settings,
                    storyboard_images=[image_input(STORYBOARD, "STORYBOARD")],
                    product_images=[image_input(PRODUCT, "PRODUCT")],
                    reference_role_prompt=_reference_role_prompt(
                        storyboard_count=1, subject_image_count=1, element_count=0
                    ),
                    creative_brief=(
                        f"{duration_seconds}-second American Meta fashion ad. A real person's mirror "
                        "selfie, no captions, speech or music. Immediate product-specific hook, "
                        "natural energetic action, genuine garment details, grounded buyer reason. "
                        + ("User confirms both cuffs and hem are non-ribbed; do not add rib-knit finishing."
                           if non_ribbed else "")
                    ),
                    brand_tone="Playfully offbeat everyday American UGC; ordinary, spontaneous and not influencer-like",
                    target_audience="U.S. casualwear shoppers who enjoy wearable art and bold expressive designs",
                    product_priorities=("real floral portrait artwork", "easy jeans outfit styling", "accurate yellow trim and fabric"),
                    forbidden_mismatches=("no unrelated graphics", "no new person or scene", "no invented dialogue"),
                    garment_material=material,
                    duration_seconds=duration_seconds, variant_count=1, cut_mode="explicit_cuts",
                    action_intensity="high", client=tracking,
                )
                one = results[0]
                print({"probe": "multimodal.planner", "passed": True,
                       "realModelCalls": tracking.responses.calls,
                       "model": tracking.responses.model,
                       "elapsedSeconds": round(time.monotonic() - started, 2),
                       "usage": tracking.responses.usage,
                       "storyboardImages": 1, "productImages": 1,
                       "beats": len(one.beats), "promptChars": len(one.prompt),
                       "beatsTimeline": [
                           [b["startSeconds"], b["endSeconds"], b["shot_role"]]
                           for b in one.beats
                       ],
                       "productMaterialObservation": {
                           "kind": tracking.responses.parsed.observations.material_type,
                           "confidence": tracking.responses.parsed.observations.material_confidence,
                           "cuffs": tracking.responses.parsed.observations.cuff_finish,
                           "hem": tracking.responses.parsed.observations.hem_finish,
                       },
                       "materialHintInPrompt": (garment_video_material_hint(material) in one.prompt) if material != "unknown" else None,
                       "visualHook": one.visual_hook, "buyerReason": one.buyer_reason}, flush=True)
                print("<<<LIVE_AI_ENGLISH_PROMPT_BEGIN>>>", flush=True)
                print(one.prompt, flush=True)
                print("<<<LIVE_AI_ENGLISH_PROMPT_END>>>", flush=True)
            except Exception as exc:
                parsed = tracking.responses.parsed
                print({"probe": "multimodal.planner", "passed": False,
                       "realModelCalls": tracking.responses.calls,
                       "elapsedSeconds": round(time.monotonic() - started, 2),
                       "usage": tracking.responses.usage,
                       "model": tracking.responses.model,
                       "schemaParsed": parsed is not None,
                       "parsedBeatCounts": [len(v.beats) for v in parsed.variants] if parsed else None,
                       "failureReason": str(exc)[:180] if type(exc).__name__ == "CreativePlannerError" else None,
                       **safe_error(exc)}, flush=True)
                if parsed is not None:
                    report = ROOT / "var" / "probe_real_ai_director_response.json"
                    report.parent.mkdir(exist_ok=True)
                    report.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
                    print({"savedStructuredResponse": str(report)}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("text", "visual", "all", "wire", "schema", "image-wire"), default="text")
    parser.add_argument("--duration", type=int, choices=range(1, 16), metavar="1..15", default=10)
    parser.add_argument("--non-ribbed", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--material", default="unknown", choices=("unknown", "short_mohair", "short_mohair_cardigan", "mesh_jersey", "washed_tee", "cotton_tee", "sweatshirt", "cotton"))
    options = parser.parse_args()
    asyncio.run(probe(options.phase, options.model, options.material, options.duration, options.non_ribbed))
