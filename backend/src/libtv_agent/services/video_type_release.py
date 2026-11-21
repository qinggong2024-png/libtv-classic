"""Release gate for new ALLWE unboxing video generation only.

Keep the unboxing director and CUT metadata adapters intact, so future
re-enablement is a deliberate, small release change. Existing accepted jobs
may finish; this gate rejects *new* submissions before uploads are claimed
or paid providers are contacted.
"""

UNBOXING_VIDEO_GENERATION_ENABLED = False


def unboxing_submission_paused(video_type: str) -> bool:
    return video_type == "unboxing" and not UNBOXING_VIDEO_GENERATION_ENABLED


UNBOXING_PAUSED_CODE = "UNBOXING_GENERATION_PAUSED"
UNBOXING_PAUSED_MESSAGE = "开箱生成功能正在完善，暂时只支持 OOTD；没有提交付费生成。"
