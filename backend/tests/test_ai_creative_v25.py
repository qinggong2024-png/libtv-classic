"""V2.5 is an explicit opt-in preview; production defaults to V2.4."""

import json
from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ai_creative_planner import (
    V25_EXPERIMENTAL_VERSION,
    V25_PROMPT_MAX_CHARS,
    CreativeDirectorResponse,
    CreativePlannerError,
    plan_creative_video,
)
from libtv_agent.services.creative_plan_v25 import (
    WireCreativeResponse,
    expand_wire_response,
)
from test_ai_creative_planner import fixture_solo_facts, fixture_solo_plan


def wire_fixture():
    facts = fixture_solo_facts()
    plan = fixture_solo_plan()
    frame = facts.storyboards[0]
    v = plan.variants[0]
    return WireCreativeResponse.model_validate({
        "frames": [{
            "f": frame.frame_order, "counted": frame.person_count_verified,
            "people": [
                {"id": p.person_id, "evidence": p.visible_evidence}
                for p in frame.people
            ],
            "props": [{"id": p.prop_id, "evidence": p.visible_evidence} for p in frame.props],
            "scene": frame.scene, "pose": frame.initial_pose,
            "crop": frame.framing, "light": frame.lighting,
            "garment": frame.garment_evidence, "space": frame.spatial_constraints,
        }],
        "product": facts.product_appearance,
        "variants": [{
            "idea": v.creative_idea, "hook": v.visual_hook,
            "reason": v.buyer_reason,
            "shots": [{
                "f": b.frame_order, "role": b.shot_role,
                "cut": b.transition, "weight": b.duration_weight,
                "why": b.purpose, "entry": b.entry_state, "move": b.action,
                "exit": b.exit_state, "cam": b.camera,
                "proof": b.product_proof, "actors": b.actor_ids,
                "partners": b.interaction_partner_ids, "props": b.used_prop_ids,
                "novelty": "none",
            } for b in v.beats],
        }],
    })


class FakeStreaming:
    def __init__(self, result, *, events=None, error=None):
        self.result = result
        self.events = events or [
            "response.created", "response.output_text.delta", "response.completed",
        ]
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __aiter__(self):
        async def iterator():
            for kind in self.events:
                yield SimpleNamespace(
                    type=kind,
                    response=SimpleNamespace(
                        status="completed", output_text=self.result.model_dump_json(),
                        usage=SimpleNamespace(input_tokens=120, output_tokens=890),
                    ),
                )
            if self.error:
                raise self.error
        return iterator()

    async def get_final_response(self):
        return SimpleNamespace(
            status="completed", output_parsed=self.result,
            usage=SimpleNamespace(input_tokens=120, output_tokens=890),
        )


class FakeClient:
    def __init__(self, result, *, error=None, events=None):
        self.calls = []
        self.result = result
        self.error = error
        self.events = events
        self.responses = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStreaming(self.result, events=self.events, error=self.error)


@pytest.mark.asyncio
async def test_v25_logs_reasoning_tokens_separately_from_json_size(caplog):
    from libtv_agent.services.ai_creative_planner import _parse_streaming_response

    class RichUsageStream(FakeStreaming):
        def __aiter__(self):
            async def events():
                async for event in super(RichUsageStream, self).__aiter__():
                    event.response.usage.output_tokens_details = SimpleNamespace(
                        reasoning_tokens=420,
                    )
                    yield event
            return events()

    class RichUsageClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return RichUsageStream(self.result)

    client = RichUsageClient(wire_fixture())
    settings = Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None)
    with caplog.at_level("INFO"):
        await _parse_streaming_response(
            client, settings,
            [{"role": "user", "content": "synthetic, no network"}],
            WireCreativeResponse, 4600,
        )
    assert len(client.calls) == 1
    assert "outputTokens=890 reasoningTokens=420" in caplog.text
    assert "maxOutputTokens=4600 model=gpt-6.1-sol effort=low" in caplog.text
    assert "jsonChars=" in caplog.text
    assert wire_fixture().variants[0].idea not in caplog.text


async def preview(client):
    return await plan_creative_video(
        settings=Settings(app_env="test", _env_file=None),
        storyboard_images=[AnalysisImageInput("img", 1, "data:image/png;base64,AA")],
        product_images=[AnalysisImageInput("product", 1, "data:image/png;base64,BB")],
        reference_role_prompt="References: @image_1 actor; @image_2 exact garment",
        creative_brief="Show realistic bookish garment detail",
        brand_tone="natural reader UGC",
        duration_seconds=10,
        variant_count=1,
        client=client,
        experimental_v25=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,configured,expected",
    [
        ("gpt-6.1-sol", "none", "low"),
        ("gpt-6.1-sol", "medium", "medium"),
        ("gpt-6.1-terra", "none", "none"),
        ("gpt-6-astra", "none", "low"),
    ],
)
async def test_v25_model_compatible_reasoning_is_sent_to_responses(
    model, configured, expected,
):
    from libtv_agent.services.ai_creative_planner import _parse_streaming_response

    client = FakeClient(wire_fixture())
    settings = Settings(
        app_env="test", openai_model=model,
        openai_reasoning_effort=configured, _env_file=None,
    )
    result = await _parse_streaming_response(
        client, settings,
        [{"role": "user", "content": "synthetic test; no external API"}],
        WireCreativeResponse, 4600,
    )
    assert isinstance(result, WireCreativeResponse)
    assert len(client.calls) == 1
    assert client.calls[0]["reasoning"] == {"effort": expected}
    assert client.calls[0]["max_output_tokens"] == 4600
    assert client.calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_compact_stream_preserves_full_plan_and_existing_guards(caplog):
    client = FakeClient(wire_fixture())
    with caplog.at_level("INFO"):
        compiled = await preview(client)
    assert len(client.calls) == 1
    from libtv_agent.services.ai_creative_planner import CREATIVE_SYSTEM_PROMPT
    from libtv_agent.services.creative_plan_v25 import WIRE_DIRECTIVE
    assert client.calls[0]["input"][0]["content"] == CREATIVE_SYSTEM_PROMPT + "\n" + WIRE_DIRECTIVE
    assert len(WIRE_DIRECTIVE) < 800
    assert client.calls[0]["stream"] is True
    assert client.calls[0]["text"]["format"]["type"] == "json_schema"
    assert client.calls[0]["max_output_tokens"] <= 4600
    assert len(compiled) == 1
    assert compiled[0].planner_version == V25_EXPERIMENTAL_VERSION
    assert len(compiled[0].beats) == 4
    assert compiled[0].visual_hook == fixture_solo_plan().variants[0].visual_hook
    assert "readers" in compiled[0].buyer_reason.lower()
    assert "reading-ghost" in compiled[0].prompt
    assert len(compiled[0].prompt) <= 3072
    assert "V2.5 stream progress" in caplog.text
    assert "outputTokens=890" in caplog.text
    assert "AI_CREATIVE_STREAM_INTERRUPTED" not in caplog.text


def test_wire_saves_schema_size_without_deleting_motion_or_proof():
    old = len(json.dumps(CreativeDirectorResponse.model_json_schema()))
    new = len(json.dumps(WireCreativeResponse.model_json_schema()))
    assert new < old
    shot_fields = WireCreativeResponse.model_fields["variants"].annotation
    assert shot_fields is not None
    expanded = expand_wire_response(wire_fixture())
    assert expanded.variants[0].beats[0].entry_state
    assert expanded.variants[0].beats[0].exit_state
    assert expanded.variants[0].beats[0].camera
    assert expanded.variants[0].beats[0].product_proof


@pytest.mark.asyncio
async def test_v25_local_content_review_does_not_reject_model_directed_people():
    wire = wire_fixture()
    wire.variants[0].shots[0].novelty = "person"
    client = FakeClient(wire)
    compiled = await preview(client)
    assert len(compiled) == 1
    assert compiled[0].beats[0]["introduces_person"] is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_stream_interruption_after_event_has_no_automatic_replay():
    client = FakeClient(wire_fixture(), error=OSError("private data"))
    with pytest.raises(CreativePlannerError) as failure:
        await preview(client)
    assert failure.value.code == "AI_CREATIVE_STREAM_INTERRUPTED"
    assert len(client.calls) == 1
    assert "private data" not in str(failure.value)


@pytest.mark.asyncio
async def test_stream_without_completion_cannot_be_used():
    client = FakeClient(
        wire_fixture(),
        events=["response.created", "response.output_text.delta"],
    )
    with pytest.raises(CreativePlannerError) as failure:
        await preview(client)
    assert failure.value.code == "AI_CREATIVE_RESPONSE_INVALID"
    assert len(client.calls) == 1



@pytest.mark.asyncio
async def test_completed_invalid_json_is_not_misreported_as_stream_interrupted(caplog):
    # A successfully completed SSE response can still violate the schema.
    # Never log the returned content, and never replay it as a transport failure.
    invalid = SimpleNamespace(
        model_dump_json=lambda: '{"frames":[],"variants":[]}'
    )
    client = FakeClient(invalid)
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as failure:
            await preview(client)
    assert failure.value.code == "AI_CREATIVE_RESPONSE_INVALID"
    assert len(client.calls) == 1
    assert "failed schema validation" in caplog.text
    assert "stream interrupted" not in caplog.text
    assert '"frames"' not in caplog.text

@pytest.mark.asyncio
async def test_terminal_error_event_after_metadata_is_not_ignored(caplog):
    # A Codex-shaped relay can send metadata followed by an upstream error.
    client = FakeClient(
        wire_fixture(),
        events=["codex.response.metadata", "error", "response.completed"],
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as failure:
            await preview(client)
    assert failure.value.code == "AI_CREATIVE_RESPONSE_INVALID"
    assert len(client.calls) == 1
    assert "terminal upstream event" in caplog.text


@pytest.mark.asyncio
async def test_explicit_upstream_overload_is_classified_without_leaking_error_body(caplog):
    class OverloadedStream(FakeStreaming):
        def __aiter__(self):
            async def iterator():
                yield SimpleNamespace(type="response.metadata")
                yield SimpleNamespace(
                    type="error",
                    error={"code": "server_is_overloaded", "message": "sensitive payload"},
                )
            return iterator()

    class OverloadedClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return OverloadedStream(self.result)

    client = OverloadedClient(wire_fixture())
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as failure:
            await preview(client)
    assert failure.value.code == "AI_CREATIVE_UPSTREAM_UNAVAILABLE"
    assert len(client.calls) == 1
    assert "safeCode=server_is_overloaded" in caplog.text
    assert "sensitive payload" not in caplog.text


@pytest.mark.asyncio
async def test_idle_after_metadata_is_timeout_not_stream_interrupted(caplog):
    class IdleStream(FakeStreaming):
        def __aiter__(self):
            async def iterator():
                yield SimpleNamespace(type="codex.response.metadata")
                raise TimeoutError("do not disclose upstream contents")
            return iterator()

    class IdleClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return IdleStream(self.result)

    client = IdleClient(wire_fixture())
    with caplog.at_level("WARNING"):
        with pytest.raises(CreativePlannerError) as failure:
            await preview(client)
    assert failure.value.code == "AI_CREATIVE_REQUEST_TIMEOUT"
    assert len(client.calls) == 1
    assert "stream inactivity timeout" in caplog.text
    assert "do not disclose upstream contents" not in caplog.text


@pytest.mark.asyncio
async def test_metadata_first_event_is_accepted():
    # This is the relay event ordering that the SDK accumulator rejected.
    client = FakeClient(
        wire_fixture(),
        events=[
            "response.metadata", "response.in_progress",
            "response.output_text.delta", "response.completed",
        ],
    )
    result = await preview(client)
    assert len(result) == 1
    assert result[0].planner_version == V25_EXPERIMENTAL_VERSION
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_stream_timeout_is_not_replayed(monkeypatch):
    class TimeoutClient(FakeClient):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("provider may still be generating")

    client = TimeoutClient(wire_fixture())
    with pytest.raises(CreativePlannerError) as failure:
        await preview(client)
    assert failure.value.code == "AI_CREATIVE_REQUEST_TIMEOUT"
    assert failure.value.request_attempts == 1
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_v25_provider_timeout_not_clamped_to_legacy_180_seconds(monkeypatch):
    from libtv_agent.services import ai_creative_planner

    original = ai_creative_planner.asyncio.timeout
    timeouts = []

    def spy_timeout(seconds):
        timeouts.append(seconds)
        return original(seconds)

    monkeypatch.setattr(ai_creative_planner.asyncio, "timeout", spy_timeout)
    client = FakeClient(wire_fixture())
    result = await preview(client)
    assert len(result) == 1
    assert len(client.calls) == 1
    # This is the actual _parse_provider deadline, not the outer task budget.
    assert len(timeouts) == 1
    assert 290.0 <= timeouts[0] <= 300.0


def test_v25_motion_direction_is_buyer_motivated_and_language_neutral():
    from libtv_agent.services.creative_plan_v25 import WIRE_DIRECTIVE

    assert "purposeful action" in WIRE_DIRECTIVE
    assert "camera follows the actor" in WIRE_DIRECTIVE.lower()
    assert "one coherent language (Chinese or English)" in WIRE_DIRECTIVE
    assert "@image_N references, literal garment text" in WIRE_DIRECTIVE
    assert "Use camera energy rather than arbitrary body poses" not in WIRE_DIRECTIVE
    assert "who wants this visible differentiator" in WIRE_DIRECTIVE
    assert "Never invent prices, delivery, scarcity" in WIRE_DIRECTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "terra_specific"),
    [("gpt-6.1-terra", True), ("gpt-6.1-sol", False)],
)
async def test_v25_causal_motion_guidance_only_changes_terra_prompt(
    model, terra_specific,
):
    from libtv_agent.services.creative_plan_v25 import (
        TERRA_MOTION_DIRECTIVE,
        WIRE_DIRECTIVE,
    )

    client = FakeClient(wire_fixture())
    compiled = await plan_creative_video(
        settings=Settings(
            app_env="test", openai_model=model, _env_file=None,
        ),
        storyboard_images=[
            AnalysisImageInput("frame", 1, "data:image/png;base64,AA"),
        ],
        product_images=[
            AnalysisImageInput("product", 1, "data:image/png;base64,BB"),
        ],
        reference_role_prompt="References: @image_1 actor; @image_2 exact garment",
        creative_brief="Reveal graphic through a motivated human action",
        brand_tone="Natural American phone UGC",
        duration_seconds=10,
        variant_count=1,
        client=client,
        experimental_v25=True,
    )
    assert len(client.calls) == 1
    assert len(compiled) == 1
    request = client.calls[0]
    instructions = request["input"][0]["content"]
    assert request["model"] == model
    assert request["stream"] is True
    assert WIRE_DIRECTIVE in instructions
    assert (TERRA_MOTION_DIRECTIVE in instructions) is terra_specific
    assert len(TERRA_MOTION_DIRECTIVE) < 600
    assert "evolving human micro-story" in TERRA_MOTION_DIRECTIVE
    assert "closing finishes" in TERRA_MOTION_DIRECTIVE
    assert "count flexible" in TERRA_MOTION_DIRECTIVE
    assert request["max_output_tokens"] <= 4600
    assert request["text"]["format"]["type"] == "json_schema"
    assert compiled[0].planner_version == V25_EXPERIMENTAL_VERSION
    assert "@image_1" in compiled[0].prompt


def test_chinese_motion_compression_keeps_a_complete_action_clause():
    from libtv_agent.services.ai_creative_planner import _word_bounded

    first_action = "她看到胸口的奔马印花后笑起来，先放低手中的饮料，让原本遮挡的图案完整露出来"
    full_action = first_action + "；然后自然向镜头迈出一步，衣摆随身体动作落回原位。"
    shortened = _word_bounded(full_action, len(first_action) + 2)

    assert shortened == first_action + "。"
    assert "奔马印花" in shortened
    assert "饮料" in shortened
    assert len(shortened) <= len(first_action) + 2

@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("storyboard_count,duration_seconds", [(1, 10), (3, 15)])
def test_v25_multishot_prompt_preserves_complete_bilingual_fields(
    language, storyboard_count, duration_seconds,
):
    from libtv_agent.services.ai_creative_planner import (
        PlannedVariantBatch,
        compile_creative_variants,
    )

    observations = fixture_solo_facts()
    original_frame = observations.storyboards[0]
    observations.storyboards = []
    for number in range(1, storyboard_count + 1):
        frame = original_frame.model_copy(deep=True)
        frame.frame_order = number
        frame.people[0].character_key = "same_reader"
        frame.people[0].match_evidence = "Same visible face, outfit and hairstyle"
        observations.storyboards.append(frame)

    batch = fixture_solo_plan()
    variant = batch.variants[0]
    source_beats = variant.beats
    frame_sequence = [1, 1, 2, 3, 3] if storyboard_count == 3 else [1] * 5
    variant.beats = []
    if language == "zh":
        detail = (
            "她在原有书架前维持手持平装书的自然姿态，身体微微向左偏转，"
            "眼神跟随书页移动，同时保持衣服印花无遮挡，并保留现场自然日光的方向。"
        )
    else:
        detail = (
            "She stays beside the same shelf with a paperback, "
            "turns slightly, and keeps the sweater print visible."
        )
    for index, frame_order in enumerate(frame_sequence):
        beat = source_beats[min(index, len(source_beats) - 1)].model_copy(deep=True)
        beat.frame_order = frame_order
        beat.transition = "start" if index == 0 else "cut"
        beat.purpose = f"Visible product-specific proof at beat {index + 1}"
        beat.entry_state = f"{index + 1}: {detail}"
        beat.action = f"{index + 1}: {detail} {detail[:36]}"
        beat.exit_state = f"{index + 1}: {detail}"
        beat.camera = f"{index + 1}: {detail}"
        beat.product_proof = f"{index + 1}: {detail}"
        variant.beats.append(beat)

    references = "References: " + ", ".join(
        f"@image_{n}" for n in range(1, storyboard_count + 1)
    ) + " keep the original actor, source frames and clothes; no new people."
    prepared = dict(
        observations=observations,
        batch=PlannedVariantBatch(variants=[variant]),
        storyboard_count=storyboard_count,
        variant_count=1,
        duration_seconds=duration_seconds,
        reference_role_prompt=references,
        brand_tone="Natural non-influencer American UGC",
    )
    result = compile_creative_variants(**prepared, experimental_v25=True)[0]
    assert len(result.prompt) <= V25_PROMPT_MAX_CHARS == 3072
    assert len(result.beats) == 5
    for original, stored in zip(variant.beats, result.beats, strict=True):
        # Source director data is lossless, even when delivery text is shorter.
        for field in ("entry_state", "action", "exit_state", "camera", "product_proof"):
            assert stored[field] == getattr(original, field)
        # Every action, camera instruction and product proof survives in Kling.
        for field in ("action", "camera", "product_proof"):
            assert getattr(original, field) in result.prompt
    for number in range(1, storyboard_count + 1):
        assert f"@image_{number}" in result.prompt

    legacy = compile_creative_variants(**prepared)[0]
    assert len(legacy.prompt) <= 3072


def test_v25_over_limit_rejects_instead_of_cutting_action():
    from libtv_agent.services.ai_creative_planner import (
        compile_creative_variants,
    )
    with pytest.raises(CreativePlannerError, match="prompt limit"):
        compile_creative_variants(
            observations=fixture_solo_facts(),
            batch=fixture_solo_plan(),
            storyboard_count=1,
            variant_count=1,
            duration_seconds=10,
            reference_role_prompt="References: @image_1 " + "actor " * 3000,
            experimental_v25=True,
        )




def _png_frame(*, alpha: bool = False) -> str:
    """Noisy generated PNG fixture exercises real image re-encoding."""
    import base64
    import io

    from PIL import Image

    frame = Image.effect_noise((540, 1900), 100).convert("RGBA" if alpha else "RGB")
    if alpha:
        frame.putpixel((0, 0), (50, 100, 150, 0))
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_v25_only_optimizes_large_png_analysis_frame():
    import base64
    import io

    from libtv_agent.services.ai_creative_planner import _optimized_storyboard_image_url
    from PIL import Image

    original = _png_frame()
    assert _optimized_storyboard_image_url(original) == original
    optimized = _optimized_storyboard_image_url(original, optimize_png=True)
    assert optimized.startswith("data:image/jpeg;base64,")
    assert len(optimized) < len(original) // 2
    with Image.open(io.BytesIO(base64.b64decode(optimized.split(",", 1)[1]))) as image:
        assert max(image.size) == 1600
        assert image.mode == "RGB"


def test_v25_png_with_real_transparency_keeps_alpha():
    import base64
    import io

    from libtv_agent.services.ai_creative_planner import _optimized_storyboard_image_url
    from PIL import Image

    original = _png_frame(alpha=True)
    optimized = _optimized_storyboard_image_url(original, optimize_png=True)
    assert optimized.startswith("data:image/png;base64,")
    assert len(optimized) <= len(original)
    with Image.open(io.BytesIO(base64.b64decode(optimized.split(",", 1)[1]))) as image:
        assert max(image.size) <= 1900
        # A PNG that becomes larger is deliberately preserved unchanged.
        if optimized != original:
            assert max(image.size) == 1600
        assert image.convert("RGBA").getchannel("A").getextrema()[0] < 255


@pytest.mark.asyncio
async def test_v25_uses_compact_png_storyboard_but_preserves_product_reference(caplog):
    from libtv_agent.services.ai_creative_planner import plan_creative_video

    source_frame = _png_frame()
    product = _png_frame()
    client = FakeClient(wire_fixture())
    with caplog.at_level("INFO"):
        compiled = await plan_creative_video(
            settings=Settings(app_env="test", _env_file=None),
            storyboard_images=[AnalysisImageInput("frame", 1, source_frame)],
            product_images=[AnalysisImageInput("garment", 1, product)],
            reference_role_prompt="References: @image_1 actor; @image_2 exact garment",
            creative_brief="Show reader shirt and natural movement",
            brand_tone="casual American UGC",
            duration_seconds=10,
            variant_count=1,
            client=client,
            experimental_v25=True,
        )
    images = [
        item["image_url"]
        for item in client.calls[0]["input"][1]["content"]
        if item["type"] == "input_image"
    ]
    assert len(compiled) == 1
    assert images[0].startswith("data:image/jpeg;base64,")
    assert len(images[0]) < len(source_frame) // 2
    assert images[1] == product
    assert "V2.5 storyboard request payload" in caplog.text
    assert len(client.calls) == 1

def test_v25_submission_length_uses_the_same_kling_ceiling():
    from libtv_agent.services.fixed_video_tasks import (
        FixedVideoTaskValidationError,
        _validate_final_prompt_length,
    )

    prompt = "x" * 6500
    with pytest.raises(FixedVideoTaskValidationError) as error:
        _validate_final_prompt_length(
            prompt, code="TOO_LONG", message="too long",
            planner_version=V25_EXPERIMENTAL_VERSION, provider_backend="api",
        )
    assert error.value.details["limit"] == 3072
    for backend, version in (
        ("api", None), ("api", "ai-multimodal-director-v2.4.1-motion-continuity"),
        ("bach", V25_EXPERIMENTAL_VERSION), ("cli", V25_EXPERIMENTAL_VERSION),
    ):
        with pytest.raises(FixedVideoTaskValidationError) as error:
            _validate_final_prompt_length(
                prompt, code="TOO_LONG", message="too long",
                planner_version=version, provider_backend=backend,
            )
        assert error.value.details["limit"] == 3072

    with pytest.raises(FixedVideoTaskValidationError) as error:
        _validate_final_prompt_length(
            "x" * (V25_PROMPT_MAX_CHARS + 1),
            code="TOO_LONG", message="too long",
            planner_version=V25_EXPERIMENTAL_VERSION, provider_backend="api",
        )
    assert error.value.details["limit"] == V25_PROMPT_MAX_CHARS


@pytest.mark.asyncio
@pytest.mark.parametrize("story_count", (2, 3, 4))
@pytest.mark.parametrize("product_count", (2, 3, 4))
@pytest.mark.parametrize("variant_count", (1, 2))
async def test_v25_multi_image_reference_matrix(
    story_count: int, product_count: int, variant_count: int,
):
    """Contract/numbering test, not a claim of real multimodal visual quality."""
    from libtv_agent.services.fixed_video_tasks import _reference_role_prompt

    wire = wire_fixture()
    frames = [
        wire.frames[0].model_copy(update={"f": index})
        for index in range(1, story_count + 1)
    ]
    frame_orders = {
        2: [1, 1, 2, 2],
        3: [1, 2, 2, 3],
        4: [1, 2, 3, 4],
    }[story_count]
    base = wire.variants[0]
    shots = [
        shot.model_copy(update={
            "f": frame_orders[index],
            "cut": "start" if index == 0 else "cut",
        })
        for index, shot in enumerate(base.shots)
    ]
    first = base.model_copy(update={"shots": shots})
    second = first.model_copy(update={
        "idea": "Alternate creative concept for the same verified garment",
        "hook": "Another visual product-specific hook",
        "reason": "A distinct wearable-art buyer reason supported by the garment",
    })
    wire = wire.model_copy(update={
        "frames": frames,
        "variants": [first, second][:variant_count],
    })
    fake = FakeClient(wire)
    storyboard = [
        AnalysisImageInput(f"frame{index}", index, "data:image/png;base64,AA", "STORYBOARD")
        for index in range(1, story_count + 1)
    ]
    product = [
        AnalysisImageInput(f"product{index}", index, "data:image/png;base64,BB", "PRODUCT")
        for index in range(1, product_count + 1)
    ]
    compiled = await plan_creative_video(
        settings=Settings(
            app_env="test", openai_model="gpt-6.1-sol", _env_file=None,
        ),
        storyboard_images=storyboard,
        product_images=product,
        reference_role_prompt=_reference_role_prompt(
            storyboard_count=story_count,
            subject_image_count=product_count,
            element_count=0,
        ),
        creative_brief="Authentic phone-shot apparel ad",
        brand_tone="natural UGC",
        duration_seconds=10,
        variant_count=variant_count,
        client=fake,
        experimental_v25=True,
    )
    assert len(fake.calls) == 1
    assert fake.calls[0]["max_output_tokens"] == 4600
    assert fake.calls[0]["reasoning"] == {"effort": "low"}
    assert len(compiled) == variant_count
    for variant in compiled:
        assert len(variant.facts) == story_count
        assert f"@image_{story_count + product_count}" in variant.prompt
        assert [beat["frame_order"] for beat in variant.beats] == frame_orders
        assert variant.planner_version == V25_EXPERIMENTAL_VERSION
