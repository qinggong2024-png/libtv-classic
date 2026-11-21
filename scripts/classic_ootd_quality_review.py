"""Read-only video review manifest and explicit human defect annotations.

The source DB is opened SQLite mode=ro. Output is an independent local JSON
sidecar, never used to approve videos or gate CUT. No video model, provider
request, schema migration, or external network access.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CODES = {
    "PRODUCT_UNCLEAR", "TEXT_CROPPED", "GARMENT_MISMATCH",
    "PERSON_REPLACED", "ACTION_ARTIFACT", "PROP_INTERACTION_ARTIFACT",
    "UNNATURAL_MOTION", "REPETITIVE_MOTION", "UNHELPFUL_SHOT",
    "SPATIAL_DISCONTINUITY", "OTHER",
}
SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VERDICTS = {"PASS", "FAIL", "REVIEW_REQUIRED"}


def _valid_uuid(value: str) -> str:
    return uuid.UUID(value).hex


def _save(path: Path, value: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Review file exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def create_manifest(database: Path, task_id: str) -> dict[str, Any]:
    """Get only data supported by the current classic DB; no assumed quality."""
    task_id = _valid_uuid(task_id)
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        parent = db.execute(
            "SELECT id,phase FROM parent_task WHERE id=?", (task_id,),
        ).fetchone()
        if parent is None:
            raise ValueError(f"Unknown parent task {task_id}")
        variants = db.execute(
            "SELECT id,variant_index,prompt_hash,generation_status,validation_status,"
            "download_status,generation_config_json FROM generation_variant "
            "WHERE parent_task_id=? ORDER BY variant_index,id", (task_id,),
        ).fetchall()
        records = []
        for variant in variants:
            attempt = db.execute(
                "SELECT id,task_id,status,provider,provider_model,credits_consumed,"
                "local_output_path,completed_at FROM generation_attempt "
                "WHERE variant_id=? ORDER BY created_at DESC LIMIT 1", (variant["id"],),
            ).fetchone()
            artifact = (
                db.execute(
                    "SELECT sha256,duration_seconds,size_bytes,width,height "
                    "FROM download_artifact WHERE attempt_id=? "
                    "ORDER BY created_at DESC LIMIT 1", (attempt["id"],),
                ).fetchone()
                if attempt else None
            )
            config = json.loads(variant["generation_config_json"] or "{}")
            records.append({
                "variantId": variant["id"],
                "variantIndex": variant["variant_index"],
                "solPromptHash": variant["prompt_hash"],
                "directorProfile": config.get("directorNativeProfile"),
                "providerTaskId": attempt["task_id"] if attempt else None,
                "provider": attempt["provider"] if attempt else None,
                "model": attempt["provider_model"] if attempt else None,
                "providerStatus": attempt["status"] if attempt else None,
                "generatedArtifact": dict(artifact) if artifact else None,
                "localOutputPath": attempt["local_output_path"] if attempt else None,
                "costUnits": attempt["credits_consumed"] if attempt else None,
                "technicalValidation": variant["validation_status"],
                "reviewVerdict": "UNREVIEWED",
                "issues": [],
                "reviewedBy": None,
                "reviewedAtUtc": None,
            })
    return {
        "schema": "allwe-ootd-human-quality-review-v1",
        "taskId": task_id,
        "taskPhaseAtSnapshot": parent["phase"],
        "createdAtUtc": datetime.now(UTC).isoformat(),
        "qualityEvaluationMode": "HUMAN_ANNOTATION_NOT_AUTOMATED",
        "impactsGeneration": False,
        "impactsCut": False,
        "variants": records,
    }


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "allwe-ootd-human-quality-review-v1":
        raise ValueError("Invalid review schema")
    return data


def _variant(data: dict[str, Any], variant_id: str) -> dict[str, Any]:
    target = _valid_uuid(variant_id)
    for item in data["variants"]:
        if _valid_uuid(item["variantId"]) == target:
            return item
    raise ValueError(f"Variant not found: {target}")


def append_issue(
    data: dict[str, Any], variant_id: str, *, code: str, severity: str,
    start: float, end: float, note: str,
) -> dict[str, Any]:
    if code not in CODES or severity not in SEVERITIES:
        raise ValueError("Invalid code or severity")
    if not 0 <= start <= end:
        raise ValueError("Invalid video time interval")
    item = _variant(data, variant_id)
    duration = (item.get("generatedArtifact") or {}).get("duration_seconds")
    if duration is not None and end > float(duration) + 0.5:
        raise ValueError("Defect interval exceeds video duration")
    item["issues"].append({
        "code": code, "severity": severity,
        "startSeconds": start, "endSeconds": end, "note": note[:500],
    })
    # A finding cannot silently count as a positive review.
    if item["reviewVerdict"] == "PASS":
        item["reviewVerdict"] = "REVIEW_REQUIRED"
    return data


def finish_review(
    data: dict[str, Any], variant_id: str, *, verdict: str, reviewer: str,
) -> dict[str, Any]:
    if verdict not in VERDICTS or not reviewer.strip():
        raise ValueError("Invalid verdict or missing reviewer")
    item = _variant(data, variant_id)
    if verdict == "PASS" and any(issue["severity"] in {"HIGH", "CRITICAL"}
                                 for issue in item["issues"]):
        raise ValueError("Cannot PASS with an unresolved high/critical defect")
    item["reviewVerdict"] = verdict
    item["reviewedBy"] = reviewer.strip()
    item["reviewedAtUtc"] = datetime.now(UTC).isoformat()
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    snap = commands.add_parser("snapshot")
    snap.add_argument("--db", type=Path, required=True)
    snap.add_argument("--task-id", required=True)
    snap.add_argument("--out", type=Path, required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("--file", type=Path, required=True)
    issue.add_argument("--variant-id", required=True)
    issue.add_argument("--code", choices=sorted(CODES), required=True)
    issue.add_argument("--severity", choices=sorted(SEVERITIES), required=True)
    issue.add_argument("--start", type=float, required=True)
    issue.add_argument("--end", type=float, required=True)
    issue.add_argument("--note", default="")
    review = commands.add_parser("review")
    review.add_argument("--file", type=Path, required=True)
    review.add_argument("--variant-id", required=True)
    review.add_argument("--verdict", choices=sorted(VERDICTS), required=True)
    review.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    if args.action == "snapshot":
        record = create_manifest(args.db, args.task_id)
        _save(args.out, record, overwrite=False)
        print(json.dumps({"taskId": record["taskId"], "variants": len(record["variants"]),
                          "manifest": str(args.out)}, ensure_ascii=False))
    else:
        record = _read(args.file)
        if args.action == "issue":
            append_issue(record, args.variant_id, code=args.code, severity=args.severity,
                         start=args.start, end=args.end, note=args.note)
        else:
            finish_review(record, args.variant_id, verdict=args.verdict,
                          reviewer=args.reviewer)
        _save(args.file, record, overwrite=True)
        print(json.dumps({"taskId": record["taskId"], "action": args.action,
                          "variantId": _valid_uuid(args.variant_id)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
