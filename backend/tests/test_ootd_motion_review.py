"""Offline semantic motion comparison: no provider submission or action-library use."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from libtv_agent.adapters.openai_analysis import AnalysisImageInput
from libtv_agent.config import Settings
from libtv_agent.services.ootd_simple import plan_simple_ootd

FIRST = (
    "10秒9:16，原首帧完整毛衣，人物轻抬双臂展示肩线，然后侧移一步。"
    "DIRECT CUT / 直接切镜，展示清晰印花与文字，手臂自然放下。"
    "DIRECT CUT / 直接切镜，人物轻侧身回望镜头，保留衣服轮廓。"
    "正常速度，无慢动作，身体与衣服自然同步运动。"
    "镜头不连续推拉，人物始终保持真实生活感。"
)
REVISED = (
    "10秒9:16，原首帧人物轻轻转动肩膀，摆手与朋友打招呼，毛衣自然垂落。"
    "DIRECT CUT / 直接切镜，镜头展示印花完整区域，前胸不受力。"
    "DIRECT CUT / 直接切镜，人物重新调整站姿，完整展示穿搭。"
    "自然速度无慢动作，保持真实街头人物神态，动作与布料垂坠同步自然。"
)


class Replies:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        next_value = next(self.values)
        if isinstance(next_value, Exception):
            raise next_value
        return SimpleNamespace(output_text=next_value)


async def _plan(replies, history=()):
    return (await plan_simple_ootd(
        settings=Settings(app_env="test", openai_model="gpt-6.1-sol", _env_file=None),
        storyboard_images=[AnalysisImageInput(
            asset_id="first", role="STORYBOARD", order=1, image_url="fixture:first",
        )],
        product_images=[],
        creative_brief="不要扯毛衣，保护文字；动作自然。",
        duration_seconds=10,
        recent_prompts=history,
        client=SimpleNamespace(responses=replies),
    ))[0]


@pytest.mark.asyncio
async def test_no_history_keeps_sol_single_call():
    replies = Replies(FIRST)
    result = await _plan(replies)
    assert result.prompt == FIRST
    assert result.request_attempts == 1
    assert len(replies.calls) == 1


@pytest.mark.asyncio
async def test_distinct_motion_does_not_retry():
    replies = Replies(FIRST, "DIFFERENT")
    result = await _plan(replies, ("前次自然走两步，回眸和轻拍包带。",))
    assert result.prompt == FIRST
    assert len(replies.calls) == 2
    checker = replies.calls[1]
    assert checker["model"] == "gpt-6.1-sol"
    text = checker["input"][1]["content"][0]["text"]
    assert "PRIOR MOTION EXAMPLES" in text
    assert FIRST in text
    assert "Output exactly REPEAT or DIFFERENT" in checker["input"][0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_clear_repeat_replans_only_once_with_same_first_frame_and_user_brief():
    replies = Replies(FIRST, "REPEAT", REVISED)
    result = await _plan(replies, ("前次双臂抬起，侧移，然后侧身回头。",))
    assert result.prompt == REVISED
    assert result.request_attempts == 2
    assert len(replies.calls) == 3
    retry_user = replies.calls[2]["input"][1]["content"]
    assert retry_user[0]["text"].find("不要扯毛衣，保护文字") >= 0
    assert any(part.get("type") == "input_image" and
               part.get("image_url") == "fixture:first" for part in retry_user)
    assert "different set of useful actions" in retry_user[1]["text"]
    assert replies.calls[2]["input"][0]["content"][0]["text"] == (
        replies.calls[0]["input"][0]["content"][0]["text"]
    )


@pytest.mark.asyncio
async def test_unknown_review_falls_back_to_valid_first_draft():
    replies = Replies(FIRST, TimeoutError("review offline"))
    result = await _plan(replies, (FIRST,))
    assert result.prompt == FIRST
    assert len(replies.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["too short", RuntimeError("retry offline")])
async def test_invalid_or_failed_retry_keeps_original(replacement):
    replies = Replies(FIRST, "REPEAT", replacement)
    result = await _plan(replies, (FIRST,))
    assert result.prompt == FIRST
    assert result.request_attempts == 2
    assert len(replies.calls) == 3


@pytest.mark.asyncio
async def test_uncertain_review_does_not_rewrite_or_force_diversity():
    replies = Replies(FIRST, "DIFFERENT")
    result = await _plan(replies, ("上次人物也站着看向镜头，切镜后胸前特写。",))
    assert result.prompt == FIRST
    assert len(replies.calls) == 2
