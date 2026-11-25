"""Reliability audit reports anomalies without changing any task or receipt."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from classic_pipeline_audit import reliability_invariants, logs_report


def test_audit_flags_invariants_without_mutating_history(tmp_path):
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
          CREATE TABLE parent_task(id, workflow_kind, phase, phase_reason_code, updated_at);
          CREATE TABLE generation_variant(id, parent_task_id, download_status);
          CREATE TABLE generation_attempt(id, variant_id, provider, status, task_id, provider_account_id);
          CREATE TABLE generation_submission_attempt(attempt_id, status, provider_task_id);
          CREATE TABLE generation_command(attempt_id, status);
          CREATE TABLE download_artifact(attempt_id, status);
          CREATE TABLE outbox_event(event_type, status);
          INSERT INTO parent_task VALUES
            ('p1','FIXED_TEMPLATE','PREPARING',NULL,'2000-01-01'),
            ('p2','DEEP_VIDEO','QUEUED',NULL,'2000-01-01'),
            ('p3','FIXED_TEMPLATE','GENERATING','AGGREGATED_ACTION_REQUIRED','2000-01-01'),
            ('p4','FIXED_TEMPLATE','QUEUED',NULL,'2000-01-01');
          INSERT INTO generation_variant VALUES ('v1','p2','NOT_READY'),
            ('v2','p3','READY'), ('v3','p4','NOT_READY');
          INSERT INTO generation_attempt VALUES
            ('a1','v1','kling_official_api','RUNNING','original','account'),
            ('a2','v2','kling_official_api','UNKNOWN_AFTER_SUBMIT',NULL,'account'),
            ('a3','v3','kling_official_api','RUNNING','original','account');
          INSERT INTO generation_submission_attempt VALUES ('a3','ACCEPTED','original');
          INSERT INTO generation_command VALUES ('a3','PROCESS_STARTED');
          INSERT INTO outbox_event VALUES ('GENERATION_COMMANDS_READY','PENDING'),
            ('DURABLE_CONFIRMATION_REQUESTED','PENDING');
        """)
    before = db.read_bytes()
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        result = reliability_invariants(conn)
        assert result["review_counts"] == {
            "stale_preparing_without_attempt": 1,
            "stale_queued_without_live_command": 1,
            "active_official_receipt_mismatch": 1,
            "duplicate_official_handle_groups": 1,
            "ready_variant_without_ready_artifact": 1,
            "stale_generating_with_generic_failure": 1,
        }
        assert result["historical_counts"] == {
            "unknown_paid_attempts": 1,
            "unknown_paid_with_handle": 0,
            "legacy_pending_ready_notifications": 1,
        }
        assert result["review_required"] is True
    assert db.read_bytes() == before


def test_audit_separates_api_slots(tmp_path):
    logs = tmp_path / "var/classic-lan/logs"
    logs.mkdir(parents=True)
    (logs / "api.log").write_text('INFO "GET /api/v1/health/ready HTTP/1.1" 503\n')
    (logs / "api-green.log").write_text('INFO "POST /api/v1/uploads HTTP/1.1" 201\n')
    result = logs_report(tmp_path, 4096)
    assert result["api_slot_log_samples"]["8002"]["request_statuses"] == {"503": 1}
    assert result["api_slot_log_samples"]["8003"]["request_statuses"] == {"201": 1}
