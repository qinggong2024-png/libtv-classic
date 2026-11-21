"""Read-only single/multi CUT diagnosis; never POSTs or prints credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from classic_cutflow import cutflow_environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from libtv_agent.config import Settings  # noqa: E402
from libtv_agent.runner.cutflow_worker import ControlHTTPError, control_request  # noqa: E402
from libtv_agent.services.cutflow_handoff import safe_path  # noqa: E402
from libtv_agent.services.cutflow_observation import load, observe  # noqa: E402
from libtv_agent.services.cutflow_protocol import (  # noqa: E402
    JOB_CONTRACT,
    STATUS_SCHEMA,
)


def handoff_key(value: str) -> str:
    if not value.startswith("handoff-"):
        raise argparse.ArgumentTypeError("Expected handoff-<UUID>")
    try:
        identifier = uuid.UUID(value[8:])
    except ValueError:
        raise argparse.ArgumentTypeError("Expected handoff-<UUID>") from None
    if value != f"handoff-{identifier}":
        raise argparse.ArgumentTypeError("Expected canonical handoff-<UUID>")
    return value


def json_file(path: Path, limit: int = 1_048_576) -> dict:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("metadata too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("invalid metadata")
    return result


def digest_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def package_check(root: Path, key: str, digest: str, verify_media: bool) -> dict:
    result = {"manifest": False, "ready": False, "mediaVerified": False}
    try:
        package = safe_path(root, f"inbox/{key}")
        manifest_path = safe_path(package, "manifest.json")
        manifest = json_file(manifest_path)
        result["manifest"] = (
            digest_file(manifest_path) == digest and manifest.get("handoff_id") == key
        )
        result["ready"] = json_file(safe_path(package, "READY")).get("sha256") == digest
        if verify_media and result["manifest"] and result["ready"]:
            files = [*manifest.get("assets", []), *manifest.get("product_references", [])]
            result["mediaVerified"] = bool(files) and all(
                (path := safe_path(package, item["relative_path"])).stat().st_size
                == item["size_bytes"] and digest_file(path) == item["sha256"]
                for item in files
            )
    except (OSError, ValueError, KeyError, TypeError):
        result["errorCode"] = "NAS_PACKAGE_UNREADABLE_OR_INVALID"
    return result


def receipt_check(
    root: Path,
    key: str,
    digest: str,
    outputs: list[dict],
) -> dict:
    path = safe_path(root, f"receipts/classic/{key}.json")
    try:
        receipt = json_file(path)
    except (OSError, ValueError):
        return {"present": False, "valid": False}
    expected_outputs = {
        (item.get("id"), item.get("sha256"), item.get("size_bytes"))
        for item in outputs
        if isinstance(item, dict)
    }
    actual_outputs = {
        (item.get("id"), item.get("sha256"), item.get("sizeBytes"))
        for item in receipt.get("outputs", [])
        if isinstance(item, dict)
    }
    valid = (
        receipt.get("schemaVersion") == "yiying-classic-import/1"
        and receipt.get("writer") == "classic"
        and receipt.get("status") == "PENDING_REVIEW"
        and receipt.get("handoffKey") == key
        and receipt.get("manifestSha256") == digest
        and expected_outputs == actual_outputs
    )
    return {
        "present": True,
        "valid": valid,
        "status": receipt.get("status"),
        "verifiedAt": receipt.get("verifiedAt"),
    }


def settings_for(storage: Path) -> Settings | None:
    environment = cutflow_environment(storage)
    if not environment:
        return None
    # Explicit local configuration only: no provider credentials or arbitrary shell env.
    return Settings(
        _env_file=None, artifact_root=storage / "artifacts",
        cutflow_handoff_enabled=True, cutflow_transport=environment["CUTFLOW_TRANSPORT"],
        cutflow_base_url=environment["CUTFLOW_BASE_URL"],
        cutflow_handoff_root=Path(environment["CUTFLOW_HANDOFF_ROOT"]),
        cutflow_service_token=environment["CUTFLOW_SERVICE_TOKEN"] or None,
    )


def diagnose(storage: Path, key: str, *, verify_media: bool = False) -> dict:
    key = handoff_key(key)
    settings = settings_for(storage)
    if settings is None:
        return {"handoffId": key, "errorCode": "CLASSIC_CUT_NOT_CONFIGURED"}
    result = {"handoffId": key, "checkedAt": datetime.now(UTC).isoformat(), "readOnly": True}
    try:
        with sqlite3.connect((storage / "classic.db").resolve().as_uri() + "?mode=ro",
                             uri=True, timeout=2) as database:
            database.row_factory = sqlite3.Row
            columns = {
                row["name"]
                for row in database.execute("PRAGMA table_info(cutflow_handoff)")
            }
            output_column = "outputs_json" if "outputs_json" in columns else "'[]' AS outputs_json"
            record = database.execute(
                "SELECT id,status,cutflow_batch_id,error_code,failure_count,next_run_at,"
                f"lease_until,manifest_json,{output_column} "
                "FROM cutflow_handoff WHERE handoff_key=?",
                (key,),
            ).fetchone()
        if record is None:
            return {**result, "errorCode": "LOCAL_HANDOFF_NOT_FOUND"}
        result["local"] = {name: record[name] for name in (
            "status", "cutflow_batch_id", "error_code", "failure_count",
            "next_run_at", "lease_until",
        )}
        descriptor = json.loads(record["manifest_json"])
        digest = descriptor.get("manifestSha256")
        result["nas"] = package_check(settings.cutflow_handoff_root, key, digest, verify_media)
        result["classicReceipt"] = receipt_check(
            settings.cutflow_handoff_root,
            key,
            digest,
            list(record["outputs_json"] and json.loads(record["outputs_json"]) or []),
        )
        try:
            response = control_request(settings, "GET", f"/{key}")
            result["http"] = {"status": 200, "identityMatches": (
                response.get("handoffId") == key and response.get("manifestSha256") == digest
            )}
            if result["http"]["identityMatches"]:
                state, warning = observe(
                    settings, load(settings, uuid.UUID(record["id"])), response, datetime.now(UTC),
                )
                result["remote"] = state
                result["blockerCode"] = warning[0] if warning else None
                result["progressHistoryAvailable"] = bool(load(settings, uuid.UUID(record["id"])))
                if not state["diagnosticsAvailable"]:
                    result["diagnosticGap"] = "CUT_RUNTIME_DIAGNOSTICS_NOT_AVAILABLE"
            else:
                result["blockerCode"] = "REMOTE_IDENTITY_MISMATCH"
        except ControlHTTPError as error:
            result["http"] = {"status": error.status_code}
            result["blockerCode"] = "CUT_HTTP_ERROR"
        except Exception:
            result["blockerCode"] = "CUT_NETWORK_OR_RESPONSE_ERROR"
    except (OSError, ValueError, sqlite3.Error, KeyError):
        result["errorCode"] = "LOCAL_STATE_UNREADABLE"
    return result


def task_uuid(value: str) -> str:
    try:
        identifier = uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected task UUID") from None
    if str(identifier) != value:
        raise argparse.ArgumentTypeError("Expected canonical task UUID")
    return value


def diagnose_job(storage: Path, task_id: str, *, verify_media: bool = False) -> dict:
    settings = settings_for(storage)
    if settings is None:
        return {"taskId": task_id, "errorCode": "CLASSIC_CUT_NOT_CONFIGURED"}
    result = {
        "taskId": task_id,
        "checkedAt": datetime.now(UTC).isoformat(),
        "readOnly": True,
    }
    try:
        with sqlite3.connect(
            (storage / "classic.db").resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=2,
        ) as database:
            database.row_factory = sqlite3.Row
            record = database.execute(
                "SELECT status,manifest_json,output_json,error_code,error_message,"
                "failure_count,next_run_at,lease_until,updated_at "
                "FROM multi_cutflow_task WHERE id=?",
                (task_id.replace("-", ""),),
            ).fetchone()
        if record is None:
            return {**result, "errorCode": "LOCAL_JOB_NOT_FOUND"}
        manifest = json.loads(record["manifest_json"])
        outputs = json.loads(record["output_json"] or "[]")
        digest = manifest.get("manifestSha256")
        handoff_key = f"multi-{task_id}"
        result["local"] = {
            name: record[name]
            for name in (
                "status",
                "error_code",
                "error_message",
                "failure_count",
                "next_run_at",
                "lease_until",
                "updated_at",
            )
        }
        status_path = safe_path(
            settings.cutflow_handoff_root,
            f"outbox/{task_id}/status.json",
        )
        try:
            remote = json_file(status_path)
        except (OSError, ValueError):
            remote = None
        status_media_ok = False
        if remote is not None:
            status_media_ok = True
            required_revision = manifest.get("contractVersion") == JOB_CONTRACT
            if required_revision and remote.get("schemaVersion") != STATUS_SCHEMA:
                status_media_ok = False
            if required_revision and (
                type(remote.get("revision")) is not int or remote["revision"] < 1
            ):
                status_media_ok = False
            if verify_media:
                for output in remote.get("outputs", []):
                    relative = output.get("relativePath", output.get("relative_path"))
                    if (
                        not isinstance(relative, str)
                        or not relative.startswith(f"outbox/{task_id}/")
                    ):
                        status_media_ok = False
                        break
                    try:
                        path = safe_path(settings.cutflow_handoff_root, relative)
                        if (
                            not path.is_file()
                            or path.stat().st_size
                            != output.get("size", output.get("sizeBytes", output.get("size_bytes")))
                            or digest_file(path) != output.get("sha256")
                        ):
                            status_media_ok = False
                            break
                    except (OSError, ValueError, KeyError):
                        status_media_ok = False
                        break
        result["cutStatus"] = (
            {
                "present": True,
                "status": remote.get("status"),
                "revision": remote.get("revision"),
                "batchId": remote.get("batchId"),
                "identityMatches": (
                    remote.get("taskId") == task_id
                    and remote.get("handoffKey") == handoff_key
                    and remote.get("manifestSha256") == digest
                ),
                "mediaVerified": status_media_ok if verify_media else None,
            }
            if remote is not None
            else {"present": False}
        )
        result["classicReceipt"] = receipt_check(
            settings.cutflow_handoff_root,
            handoff_key,
            digest,
            outputs,
        )
    except (OSError, ValueError, sqlite3.Error, KeyError):
        result["errorCode"] = "LOCAL_STATE_UNREADABLE"
    if result.get("classicReceipt", {}).get("valid"):
        result["blockerCode"] = None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--handoff", type=handoff_key)
    target.add_argument("--job", type=task_uuid)
    parser.add_argument("--verify-media", action="store_true",
                        help="Also read and hash the selected video and product references")
    args = parser.parse_args()
    storage = ROOT / "var" / "classic-lan"
    result = (
        diagnose(storage, args.handoff, verify_media=args.verify_media)
        if args.handoff
        else diagnose_job(storage, args.job, verify_media=args.verify_media)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
