"""Run the existing video worker inside the independent classic LAN lifecycle."""

import os
import sys

from classic_lan import ROOT, STORAGE, generation_authorized, lan_environment
from classic_worker_lease import exclusive_classic_worker


def main() -> None:
    if not generation_authorized():
        raise SystemExit("Classic generation is not authorized; worker was not started.")
    # Only the LAN launcher may supply the exact, isolated database and storage.
    expected = lan_environment(8002, 5176, "192.168.30.126")
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
    ):
        if os.environ.get(key) != expected[key]:
            raise SystemExit(f"Classic worker requires its isolated launcher environment: {key}")
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from libtv_agent.runner import local_runner

    local_runner.RUNNER_LOG_PATH = STORAGE / "logs" / "runner-events.log"
    with exclusive_classic_worker("runner", STORAGE / "runtime"):
        local_runner.main()


if __name__ == "__main__":
    main()
