from libtv_agent.services.prompt_compiler import PromptCompileInput, compile_video_plan
from libtv_agent.templates.site_style_profiles import (
    get_site_style_profile,
    list_site_style_profiles,
)
from libtv_agent.templates.video_templates import get_video_template


STYLE_MARKERS = (
    "冷",
    "暖",
    "亲切",
    "热情",
    "轻松",
    "好奇",
    "自信",
    "专注",
    "明亮",
    "平和",
    "舒展",
    "神秘",
    "明快",
    "机灵",
    "利落",
    "从容",
    "活泼",
    "有趣",
    "日常",
    "有活力",
    "力量",
)


def test_all_active_brand_profiles_use_concise_emotion_and_style() -> None:
    profiles = tuple(
        profile for profile in list_site_style_profiles() if profile.status == "active"
    )

    assert len(profiles) == 13
    for profile in profiles:
        assert any(marker in profile.expression_language for marker in STYLE_MARKERS), (
            profile.id
        )
        assert any(marker in profile.pacing for marker in STYLE_MARKERS), profile.id
        assert len(profile.expression_language) <= 40, profile.id
        assert len(profile.pacing) <= 40, profile.id
        assert profile.forbidden_mismatches == (), profile.id
        assert profile.forbidden_motion == (), profile.id


def test_brand_style_compiles_as_expression_not_negative_content_prompt() -> None:
    template = get_video_template("garment_detail")
    for profile in list_site_style_profiles():
        if profile.status != "active":
            continue
        compiled = compile_video_plan(
            PromptCompileInput(
                template=template,
                storyboard_count=1,
                result_count=1,
                action_intensity="standard",
                camera_tempo="standard",
                cut_mode="explicit_cuts",
                variant_index=1,
                site_profile=profile,
            )
        )

        assert (
            profile.expression_language in compiled.master_prompt
            or profile.tone in compiled.master_prompt
        ), profile.id
        assert "standard_to_fast" not in compiled.master_prompt, profile.id


def test_teacher_brand_uses_a_natural_sharing_emotion() -> None:
    profile = get_site_style_profile("teachersgram")

    assert profile.expression_language == (
        "热情洋溢，像真实的老师自然分享自己的穿搭。"
    )
    assert profile.pacing == "热情活泼，像真实的老师分享日常穿搭"


def test_tokyo_tiger_no_longer_contains_legacy_content_prompts() -> None:
    profile = get_site_style_profile("tokyotiger")
    profile_text = "；".join(
        (
            profile.tone,
            profile.expression_language,
            profile.opening_rule,
            profile.middle_rule,
            profile.ending_rule,
            profile.pacing,
            *profile.forbidden_mismatches,
        )
    )

    removed_content_prompts = (
        "鞠躬",
        "武士",
        "动漫角色扮演",
        "霓虹赛博",
        "原宿",
        "武器",
        "危险道具",
        "手部遮挡",
        "快切",
    )
    assert all(prompt not in profile_text for prompt in removed_content_prompts)
