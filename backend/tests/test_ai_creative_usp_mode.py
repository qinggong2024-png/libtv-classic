"""Zero-cost USP switch regression: no additional model fields or video calls."""
from __future__ import annotations

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    CREATIVE_SYSTEM_PROMPT,
    V25_PROMPT_MAX_CHARS,
    plan_creative_video,
)
from libtv_agent.services.creative_plan_v25 import (
    TERRA_MOTION_DIRECTIVE,
    USP_WIRE_DIRECTIVE,
    WIRE_DIRECTIVE,
)
from test_ai_creative_v25 import FakeClient, wire_fixture
from test_creative_v25_admin_preview import body

pytest_plugins = ('test_creative_v25_admin_preview',)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6.1-terra"])
async def test_usp_mode_replaces_wire_not_adds_model_fields(model):
    assert len(USP_WIRE_DIRECTIVE) <= len(WIRE_DIRECTIVE)
    assert "roughly 3s" in USP_WIRE_DIRECTIVE
    assert "actor reaction" in USP_WIRE_DIRECTIVE
    assert "Buyer reason:" in USP_WIRE_DIRECTIVE
    assert "Never invent price" in USP_WIRE_DIRECTIVE
    assert "separate CTA" in USP_WIRE_DIRECTIVE
    clients = [FakeClient(wire_fixture()), FakeClient(wire_fixture())]
    results = []
    for mode, client in zip(("showcase", "usp"), clients, strict=True):
        result = await plan_creative_video(
            settings=Settings(
                app_env="test", openai_model=model,
                ai_adapter_mode="openai", openai_api_key="synthetic",
                _env_file=None,
            ),
            storyboard_images=[AnalysisImageInput("s", 1, "data:image/png;base64,AA")],
            product_images=[AnalysisImageInput("p", 1, "data:image/png;base64,BB")],
            reference_role_prompt="References: @image_1 actor; @image_2 garment",
            creative_brief="Show real garment artwork, no unsupported discounts",
            brand_tone="authentic phone UGC",
            duration_seconds=10, variant_count=1,
            client=client, experimental_v25=True, marketing_mode=mode,
        )
        results.append(result)
        assert len(client.calls) == 1
        assert client.calls[0]["stream"] is True
        assert client.calls[0]["max_output_tokens"] == 4250
        assert client.calls[0]["text"]["format"]["type"] == "json_schema"
        assert len(result[0].prompt) <= V25_PROMPT_MAX_CHARS
        assert all(result[0].beats)
    normal_call, usp_call = [c.calls[0] for c in clients]
    assert normal_call["max_output_tokens"] == usp_call["max_output_tokens"]
    assert normal_call["text"] == usp_call["text"]
    assert normal_call["input"][1] == usp_call["input"][1]
    assert len(results[0][0].beats) == len(results[1][0].beats)
    normal_text = normal_call["input"][0]["content"]
    usp_text = usp_call["input"][0]["content"]
    assert normal_text.startswith(CREATIVE_SYSTEM_PROMPT)
    assert usp_text.startswith(CREATIVE_SYSTEM_PROMPT)
    assert normal_text.count(WIRE_DIRECTIVE) == 1
    assert usp_text.count(USP_WIRE_DIRECTIVE) == 1
    assert len(usp_text) <= len(normal_text)
    if model == "gpt-6.1-terra":
        assert TERRA_MOTION_DIRECTIVE in normal_text
        assert TERRA_MOTION_DIRECTIVE in usp_text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["showcase", "usp"])
async def test_admin_preview_mode_requires_confirmed_cost_and_no_kling(
    preview_app, mode,
):
    _app, client, task_id, root_id, _member, calls, _images = preview_app
    endpoint = "/api/v1/admin/creative-v25-preview"
    result = await client.post(
        endpoint,
        headers={"X-User-Id": str(root_id)},
        json=body(task_id, creativeMode=mode),
    )
    assert result.status_code == 200, result.text
    assert result.json()["creativeMode"] == mode
    assert result.json()["klingSubmitted"] is False
    assert len(calls) == 1
    assert calls[0]["marketing_mode"] == mode
    bad = await client.post(
        endpoint,
        headers={"X-User-Id": str(root_id)},
        json=body(task_id, creativeMode=mode, confirmModelCost=False),
    )
    assert bad.status_code == 422
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_usp_invalid_mode_is_rejected_before_model(preview_app):
    _app, client, task_id, root_id, _member, calls, _images = preview_app
    r = await client.post(
        "/api/v1/admin/creative-v25-preview",
        headers={"X-User-Id": str(root_id)},
        json=body(task_id, creativeMode="override_everything"),
    )
    assert r.status_code == 422
    assert not calls
