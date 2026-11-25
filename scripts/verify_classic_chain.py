"""Run the isolated classic generation-chain regression gate without paid integration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = (
    "backend/tests/test_sol_image_transport.py",
    "backend/tests/test_kling_optional_billing.py",
    "backend/tests/test_cutflow_downloads.py",
    "backend/tests/test_upload_diagnostics.py",
    "backend/tests/test_preprocessing.py",
    "backend/tests/test_kling_submission_durability.py",
    "backend/tests/test_kling_cli_receipt_evidence.py",
    "backend/tests/test_kling_migrated_submission.py",
    "backend/tests/test_deep_video.py",
    "backend/tests/test_kling_submission_reconciliation.py",
    "backend/tests/test_classic_kling_migration.py",
    "backend/tests/test_generation_chain_resilience.py",
    "backend/tests/test_runner_heartbeat_transaction.py",
    "backend/tests/test_classic_chain_doctor.py",
    "backend/tests/test_g1_delivery_recovery.py",
    "backend/tests/test_g1_download_faults.py",
    "backend/tests/test_g1_polling_race.py",
    "backend/tests/test_g1_dispatch_workflow.py",
    "backend/tests/test_g1_delivery_settlement_crash.py",
    "backend/tests/test_g1_settlement_race.py",
    "backend/tests/test_g1_cancel_race.py",
    "backend/tests/test_g1_claim_fence.py",
    "backend/tests/test_g1_confirmation_idempotency.py",
    "backend/tests/test_g1_confirmation_recovery.py",
    "backend/tests/test_g1_batch_confirmation_recovery.py",
    "backend/tests/test_g1_image_polling_race.py",
    "backend/tests/test_image_quota_concurrency.py",
    "backend/tests/test_subject_element_jobs.py",
    "backend/tests/test_fixed_template_foundation.py",
    "backend/tests/test_kling_cli_adapter.py",
    "backend/tests/test_media.py",
    "backend/tests/test_media_relaxed_delivery.py",
    "backend/tests/test_workflow_integration.py",
    "backend/tests/test_windows_service.py",
    "backend/tests/test_launch_started_recovery.py",
    "scripts/test_classic_lan.py",
    "scripts/test_classic_autostart.py",
)


def main() -> int:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "OBJECT_STORE_MODE": "local",
            "LIBTV_ADAPTER_MODE": "fake",
            "LIBTV_EXECUTION_MODE": "dry-run",
            "PAID_GENERATION_ENABLED": "false",
            "PAID_GENERATION_CONFIRMATION": "",
            "KLING_CLI_ENABLED": "false",
            "KLING_ENABLE_PAID_INTEGRATION_TEST": "false",
            "KLING_API_ENABLE_PAID_INTEGRATION_TEST": "false",
            "PHASE2_ONLINE_TESTS": "0",
        }
    )
    # Optional PostgreSQL uses a separately provisioned disposable g1_safety DB.
    env.pop("G1_TEST_POSTGRES_URL", None)
    report = ROOT / "var/reports/classic-chain-regression.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    # A short path also avoids Windows MAX_PATH failures in deeply nested
    # artifact keys. Never reuse a prior run's database or remove the parent.
    scratch_root = Path(ROOT.anchor) / "Temp" if os.name == "nt" else Path(tempfile.gettempdir())
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cc-", dir=scratch_root) as scratch:
        assert Path(scratch).resolve().is_relative_to(scratch_root.resolve())
        env["TEMP"] = env["TMP"] = scratch
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *TEST_FILES,
                "-ra",
                "--tb=short",
                "-o",
                "faulthandler_timeout=90",
                "--junitxml",
                str(report),
                "--basetemp",
                str(Path(scratch) / "pytest"),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
