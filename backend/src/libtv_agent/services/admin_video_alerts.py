"""Evidence-based, read-only health hints for saved clips.

Only persisted signals are labelled. This does NOT assess garment fidelity,
hand artifacts or aesthetics. No provider interaction, model call or retry.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import ceil
from typing import Any

SEVERITY_RANK = {"critical": 0, "warning": 1, "normal": 2}
FAILED_CUT_STATUSES = {"FAILED"}
WARNING_CUT_STATUSES = {"UNCERTAIN", "NEEDS_MANUAL_REVIEW"}
FAILED_VALIDATION_STATUSES = {"FAILED"}
WARNING_VALIDATION_STATUSES = {"WARNING", "MANUAL_REVIEW"}


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    result = (end - start).total_seconds()
    return round(result, 2) if result >= 0 else None


def _code(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _mode(config: Any) -> str:
    if not isinstance(config, dict):
        return "unknown"
    return str(config.get("directorMode") or config.get("generationMode") or "unknown")


def _p90(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * 0.9) - 1)]


def _reason(
    code: str, level: str, title: str, stage: str, detail: str,
    next_step: str, evidence: str,
) -> dict[str, str]:
    return {
        "code": code, "level": level, "title": title, "stage": stage,
        "detail": detail, "nextStep": next_step, "evidence": evidence,
    }


def classify_saved_videos(
    rows: list[dict],
    validation_latest: dict[str, dict],
    cut_latest: dict[str, dict],
    baseline_rows: list[dict] | None = None,
) -> list[dict]:
    """Classify all matching clips before global sorting/pagination.

    Compare genuine timestamp spans against the p90 for the same provider and
    director mode. Require >=20 peers; never infer a slow stage from missing
    timestamps, and never equate a long span with proven provider fault.
    """
    measurements: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        group = (_code(row["provider"]), _mode(row.get("generation_config")))
        intervals = {
            "provider": _seconds(row.get("submitted_at"), row.get("completed_at")),
            "before_submit": _seconds(row.get("confirmed_at"), row.get("submitted_at")),
            "saved_record": _seconds(row.get("completed_at"), row.get("created_at")),
        }
        row["_spans"] = intervals
        row["_group"] = group
    # A stable, independent historical cohort prevents search/brand/date
    # filters from changing the severity of the same clip.
    cohort = rows if baseline_rows is None else baseline_rows
    for history_row in cohort:
        group = (
            _code(history_row["provider"]),
            _mode(history_row.get("generation_config")),
        )
        intervals = {
            "provider": _seconds(
                history_row.get("submitted_at"), history_row.get("completed_at")
            ),
            "before_submit": _seconds(
                history_row.get("confirmed_at"), history_row.get("submitted_at")
            ),
            "saved_record": _seconds(
                history_row.get("completed_at"), history_row.get("created_at")
            ),
        }
        for stage, seconds in intervals.items():
            if seconds is not None:
                measurements[(*group, stage)].append(seconds)

    stage_labels = {
        "provider": ("云端生成与轮询", "generation_attempt.submitted_at/completed_at"),
        "before_submit": (
            "确认后准备与排队",
            "generation_variant.confirmed_at/generation_attempt.submitted_at",
        ),
        "saved_record": (
            "生成完成至保存记录建立",
            "generation_attempt.completed_at/download_artifact.created_at",
        ),
    }
    for row in rows:
        findings: list[dict[str, str]] = []
        attempt_id = str(row["attempt_id"])
        artifact_id = str(row["artifact_id"])
        validation = validation_latest.get(attempt_id)
        v_status = (
            _code(validation["status"]) if validation
            else _code(row.get("validation_status"))
        )
        if (
            v_status in FAILED_VALIDATION_STATUSES
            or _code(row.get("validation_status")) == "FAILED"
        ):
            findings.append(_reason(
                "VALIDATION_FAILED", "critical", "原片技术校验失败", "原片校验",
                "系统保存了校验失败状态；这不等于已经确定具体画面缺陷。",
                "展开生产链路核对校验原因，确认原片是否可用。",
                "validation_result.status / generation_variant.validation_status",
            ))
        elif (
            v_status in WARNING_VALIDATION_STATUSES
            or _code(row.get("validation_status")) in WARNING_VALIDATION_STATUSES
        ):
            findings.append(_reason(
                "VALIDATION_REVIEW", "warning", "原片校验需要人工确认", "原片校验",
                "系统校验存在警告或待人工复核。",
                "播放原片并对照参考图确认商品与人物动作。",
                "validation_result.status / generation_variant.validation_status",
            ))

        if _code(row.get("final_approval_status")) == "REJECTED":
            findings.append(_reason(
                "HUMAN_REJECTED", "critical", "原片已被人工拒绝", "人工审核",
                "该视频有明确的人工拒绝记录。",
                "查看审核意见并人工决定后续处理，不自动重生成。",
                "generation_variant.final_approval_status",
            ))

        cut = cut_latest.get(artifact_id)
        if cut and _code(cut["status"]).upper() in FAILED_CUT_STATUSES:
            findings.append(_reason(
                "CUT_FAILED", "critical", "关联送剪任务失败", "CUT 送剪",
                "与此原片相关的最新送剪记录显示失败；原片本身不一定生成失败。",
                "核对 CUT 接单、原片文件和失败回执，先排除重复投递。",
                cut["source"],
            ))
        elif cut and _code(cut["status"]).upper() in WARNING_CUT_STATUSES:
            findings.append(_reason(
                "CUT_UNCERTAIN", "warning", "送剪回执待确认", "CUT 送剪",
                "最近的关联送剪状态需要核实。",
                "检查 CUT 任务 ID 与回执后再决定下一步。",
                cut["source"],
            ))

        config = row.get("generation_config") or {}
        if not isinstance(config, dict):
            config = {}
        if (
            config.get("directorMode") == "reference_guided"
            and not config.get("referenceImageBindings")
        ):
            findings.append(_reason(
                "BINDINGS_MISSING", "warning", "参考图绑定记录缺失", "参考图 / 编排",
                "当前保存的配置没有参考图绑定列表，无法仅凭配置证明当时传了哪些参考图。",
                "展开生产链路，对照任务素材与提示词，必要时核对原始请求日志。",
                "generation_variant.generation_config_json.referenceImageBindings",
            ))

        if row.get("submitted_at") is None or row.get("completed_at") is None:
            findings.append(_reason(
                "GENERATION_TIMES_MISSING", "warning", "生成时间记录不完整", "生成时间线",
                "这条已保存视频缺少供应商提交或完成时间，无法准确计算该段耗时。",
                "检查提交回执和时间线。缺失记录不代表生成失败。",
                "generation_attempt.submitted_at/completed_at",
            ))

        for stage, seconds in row["_spans"].items():
            key = (*row["_group"], stage)
            history = measurements[key]
            if seconds is None or len(history) < 20:
                continue
            p90 = _p90(history)
            threshold = max(1.6 * p90, p90 + 90)
            if seconds <= threshold:
                continue
            title, source = stage_labels[stage]
            findings.append(_reason(
                f"SLOW_{stage.upper()}", "warning", f"{title}明显偏长", title,
                (
                    f"当前约 {round(seconds)} 秒；同供应商、同编排模式 "
                    f"{len(history)} 条可比较记录的 P90 约 {round(p90)} 秒。"
                ),
                "查看该阶段前后的真实时间节点；时间长不代表已确认是供应商故障。",
                source,
            ))

        findings.sort(key=lambda x: (SEVERITY_RANK[x["level"]], x["code"]))
        severity = findings[0]["level"] if findings else "normal"
        row["health"] = {
            "severity": severity,
            "primary": findings[0] if findings else None,
            "reasons": findings,
            "qualityStatus": (
                "rejected" if _code(row.get("final_approval_status")) == "REJECTED"
                else "approved" if _code(row.get("final_approval_status")) in {
                    "QUALIFIED", "APPROVED"
                }
                else "not_reviewed"
            ),
            "message": (
                "有明确失败记录，请优先核实" if severity == "critical"
                else "有可疑环节，建议检查" if severity == "warning"
                else "暂未发现已记录的链路异常，仍需人工检查视频画面"
            ),
        }
    return rows
