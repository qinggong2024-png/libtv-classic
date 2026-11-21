"""Shared version and monotonic-status guards for the CUT wire contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from libtv_agent.services.cutflow_handoff import CutflowHandoffError

LEGACY_JOB_CONTRACT = "libtv-cut-lan-multi/1"
JOB_CONTRACT = "libtv-cut-lan-multi/2"
STATUS_SCHEMA = "yiying-cut-status/2"


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def status_fingerprint(payload: dict) -> str:
    """Hash only business state, excluding volatile diagnostics and timestamps."""

    receipt = payload.get("policyReceipt", payload.get("editPolicyReceipt"))
    material = {
        "handoffKey": payload.get("handoffKey"),
        "taskId": payload.get("taskId"),
        "manifestSha256": payload.get("manifestSha256"),
        "status": payload.get("status"),
        "batchId": payload.get("batchId"),
        "policyReceipt": receipt,
        "outputs": payload.get("outputs", []),
        "error": payload.get("error"),
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RevisionCheck:
    revision: int
    fingerprint: str
    stale: bool


def check_status_revision(
    manifest: dict,
    payload: dict,
    *,
    required: bool,
) -> RevisionCheck:
    """Reject malformed state and prevent an older snapshot from winning.

    The legacy contract had no revision. A missing revision is therefore read as
    revision 0 only when ``required`` is false. New jobs require a positive
    integer, so a restarted or retried CUT writer cannot silently publish a
    stale failure after a newer success.
    """

    schema = payload.get("schemaVersion")
    if required and schema != STATUS_SCHEMA:
        raise CutflowHandoffError(
            "INVALID_STATUS_SCHEMA",
            "CUT 状态缺少受支持的状态版本",
        )
    if schema is not None and schema != STATUS_SCHEMA:
        raise CutflowHandoffError("INVALID_STATUS_SCHEMA", "CUT 状态版本不受支持")

    raw_revision = payload.get("revision")
    if raw_revision is None:
        if required:
            raise CutflowHandoffError("INVALID_STATUS_REVISION", "CUT 状态缺少 revision")
        revision = 0
    elif type(raw_revision) is not int or raw_revision < 1:
        raise CutflowHandoffError("INVALID_STATUS_REVISION", "CUT 状态 revision 无效")
    else:
        revision = raw_revision

    fingerprint = status_fingerprint(payload)
    previous = manifest.get("_lastCutRevision")
    previous_fingerprint = manifest.get("_lastCutSnapshotSha256")
    if type(previous) is int and previous >= 1:
        if revision < previous:
            return RevisionCheck(revision, fingerprint, True)
        if revision == previous and previous_fingerprint not in {None, fingerprint}:
            raise CutflowHandoffError(
                "INVALID_STATUS_REVISION",
                "CUT 在相同 revision 下改变了任务状态",
            )
    return RevisionCheck(revision, fingerprint, False)


def remember_status_revision(manifest: dict, check: RevisionCheck) -> dict:
    if check.revision < 1:
        return dict(manifest)
    return {
        **manifest,
        "_lastCutRevision": check.revision,
        "_lastCutSnapshotSha256": check.fingerprint,
    }
