"""V2 canary release gate must be read-only and fail closed on active tasks."""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "classic_v2_canary_preflight.py"
SPEC = importlib.util.spec_from_file_location("classic_v2_canary_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_test_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE parent_task (phase TEXT, analysis_stage TEXT);
            CREATE TABLE generation_attempt (
                status TEXT, last_error_code TEXT, variant_id TEXT
            );
            CREATE TABLE generation_command (status TEXT);
            CREATE TABLE generation_variant (id TEXT, download_status TEXT);
            CREATE TABLE multi_cutflow_task (status TEXT);
            INSERT INTO generation_attempt (status,variant_id)
            VALUES ('UNKNOWN_AFTER_SUBMIT','v1'),('UNKNOWN_AFTER_SUBMIT','v2');
            INSERT INTO generation_command (status)
            VALUES ('UNCERTAIN'),('UNCERTAIN');
            """
        )


def test_historical_paid_unknown_does_not_trigger_blind_resubmit_or_false_idle_block(
    tmp_path,
):
    path = tmp_path / "canary.db"
    _make_test_db(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    report = MODULE.check(path, baseline_uncertain=2)
    assert report["quiescentForVersionSwitch"]
    assert report["counts"]["uncertainPaidHistorical"] == 2
    assert report["counts"]["uncertainCommandsHistorical"] == 2
    assert not report["blockers"]
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_active_sol_provider_and_cut_prevent_unapproved_switch(tmp_path):
    path = tmp_path / "canary.db"
    _make_test_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO parent_task VALUES ('ANALYZING','RUNNING')"
        )
        connection.execute(
            "INSERT INTO generation_attempt (status) VALUES ('RUNNING')"
        )
        connection.execute(
            "INSERT INTO generation_command VALUES ('PROCESS_STARTED')"
        )
        connection.execute(
            "INSERT INTO multi_cutflow_task VALUES ('EDITING')"
        )
    report = MODULE.check(path, baseline_uncertain=2)
    assert not report["quiescentForVersionSwitch"]
    assert set(report["blockers"]) == {
        "PREPAID_PLANNING_IN_FLIGHT",
        "CLOUD_ATTEMPTS_IN_FLIGHT",
        "PAID_COMMANDS_IN_FLIGHT",
        "CUT_JOBS_IN_FLIGHT",
    }


def test_accepted_cut_queue_blocks_version_switch_even_when_no_paid_tasks(tmp_path):
    """A CUT job accepted into the remote queue is not yet safe to discard."""
    path = tmp_path / "canary.db"
    _make_test_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO multi_cutflow_task VALUES ('ACCEPTED')")
    report = MODULE.check(path, baseline_uncertain=2)
    assert not report["quiescentForVersionSwitch"]
    assert report["counts"]["activeCutMulti"] == 1
    assert report["cutActiveByStatus"] == {"ACCEPTED": 1}
    assert "CUT_JOBS_IN_FLIGHT" in report["blockers"]


def test_unknown_increase_always_blocks_switch(tmp_path):
    path = tmp_path / "canary.db"
    _make_test_db(path)
    report = MODULE.check(path, baseline_uncertain=1)
    assert not report["quiescentForVersionSwitch"]
    assert "PAID_UNKNOWN_INCREASED" in report["blockers"]


def test_no_multi_cut_table_is_backward_compatible(tmp_path):
    path = tmp_path / "canary.db"
    _make_test_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE multi_cutflow_task")
    report = MODULE.check(path, baseline_uncertain=2)
    assert report["counts"]["activeCutMulti"] == 0
