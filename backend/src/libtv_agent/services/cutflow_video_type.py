"""Stable video-creative type for CUT; legacy and unknown values stay OOTD."""


def normalize_video_type(value: object) -> str:
    return value if isinstance(value, str) and value in {"ootd", "unboxing"} else "ootd"


def task_video_type(task: object) -> str:
    settings = getattr(task, "settings_json", None)
    if not isinstance(settings, dict):
        return "ootd"
    return normalize_video_type(settings.get("videoType", settings.get("video_type")))
