"""Run the existing video worker inside the independent classic LAN lifecycle."""

import os
import sys

from classic_lan import ROOT, STORAGE, configured_lan_ip, generation_authorized, lan_environment
from classic_worker_lease import exclusive_classic_worker


def main() -> None:
    if not generation_authorized():
        raise SystemExit("Classic generation is not authorized; worker was not started.")
    # Only the LAN launcher may supply the exact, isolated database and storage.
    expected = lan_environment(8002, 5176, configured_lan_ip() or "127.0.0.1")
    for key in (
        "DATABASE_URL",
        "RUNNER_ROOT",
        "ARTIFACT_ROOT",
        "SESSION_SECRET",
        "KLING_ADMIN_CONFIG_FILE",
        "WAVESPEED_ADMIN_CONFIG_FILE",
        "BACH_ADMIN_CONFIG_FILE",
        "LIBTV_EXECUTION_MODE",
        "PAID_GENERATION_ENABLED",
        "PAID_GENERATION_CONFIRMATION",
        "CLASSIC_WORKER_LEASE_DIR",
        "CLASSIC_DURABLE_CONFIG_FILE",
        "CLASSIC_DURABLE_CONFIG_REVISION",
        "CLASSIC_DURABLE_ANALYSIS_ENABLED",
        "CLASSIC_DURABLE_CONFIRMATION_ENABLED",
        "CLASSIC_DURABLE_ANALYSIS_CONCURRENCY",
    ):
        if os.environ.get(key) != expected[key]:
            raise SystemExit(f"Classic worker requires its isolated launcher environment: {key}")
    from classic_worker_source import worker_source

    release, revision = worker_source("runner", STORAGE / "runtime", ROOT)
    sys.path.insert(0, str(release / "backend" / "src"))
    from libtv_agent.runner import local_runner
    from libtv_agent.runner.classic_checkpoint import CheckpointStop

    local_runner.RUNNER_LOG_PATH = STORAGE / "logs" / "runner-events.log"
    with exclusive_classic_worker("runner", STORAGE / "runtime"):
        os.environ["CLASSIC_DURABLE_RUNNER_SINGLETON"] = "1"
        stopping = CheckpointStop(STORAGE / "runtime", "runner", revision)
        local_runner.main(stopping)


if __name__ == "__main__":
    main()
