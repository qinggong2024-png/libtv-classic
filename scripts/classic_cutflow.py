"""Classic-only CUT configuration and worker; never inherit another site's secrets."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def cutflow_environment(storage: Path) -> dict[str, str]:
    path = storage / "runtime" / "cutflow-config.json"
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        allowed = {"version", "enabled", "transport", "baseUrl", "handoffRoot", "ootdTemplateId",
                   "autoSubmitAfter", "autoSubmit", "nasStatusEnabled", "nasScanSeconds",
                   "multiSourceEnabled", "repeatReferenceEnabled"}
        if (not isinstance(config, dict) or set(config) - allowed
                or type(config.get("version")) is not int or config["version"] != 1
                or type(config.get("enabled")) is not bool):
            raise ValueError
        if not config["enabled"]:
            return {}
        transport = config.get("transport", "bridge")
        if transport not in {"bridge", "lan"}:
            raise ValueError
        nas_status = config.get("nasStatusEnabled", False)
        nas_scan = config.get("nasScanSeconds", 60)
        multi_source = config.get("multiSourceEnabled", False)
        repeat_reference = config.get("repeatReferenceEnabled", False)
        if (type(nas_status) is not bool
                or type(nas_scan) is not int or not 5 <= nas_scan <= 300
                or type(multi_source) is not bool
                or type(repeat_reference) is not bool
                or nas_status and transport != "lan"):
            raise ValueError
        address = urlsplit(config["baseUrl"])
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.username or address.password or address.query or address.fragment
                or address.path not in {"", "/"}):
            raise ValueError
        root = Path(config["handoffRoot"])
        if not root.is_absolute():
            raise ValueError
        if transport == "bridge":
            template = uuid.UUID(config["ootdTemplateId"])
            if template.int == 0 or str(template) != config["ootdTemplateId"]:
                raise ValueError
        cutover = config.get("autoSubmitAfter")
        auto_submit = config.get("autoSubmit", False)
        if type(auto_submit) is not bool or (auto_submit and cutover is None):
            raise ValueError
        if cutover is not None:
            timestamp = datetime.fromisoformat(cutover)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError
        token_path = storage / "runtime" / "cutflow-service-token.txt"
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
        if ((transport == "bridge" or token_path.exists())
                and (len(token) < 16 or any(character.isspace() for character in token))):
            raise ValueError
        return {
            "CUTFLOW_HANDOFF_ENABLED": "true",
            "CUTFLOW_AUTO_EDIT_ENABLED": str(auto_submit).lower(),
            "CUTFLOW_TRANSPORT": transport,
            "CUTFLOW_NAS_STATUS_ENABLED": str(nas_status).lower(),
            "CUTFLOW_NAS_SCAN_SECONDS": str(nas_scan),
            "CUTFLOW_MULTI_SOURCE_ENABLED": str(multi_source).lower(),
            "CUTFLOW_REPEAT_REFERENCE_ENABLED": str(repeat_reference).lower(),
            "CUTFLOW_BASE_URL": config["baseUrl"].rstrip("/"),
            "CUTFLOW_HANDOFF_ROOT": str(root),
            "CUTFLOW_SERVICE_TOKEN": token,
            "CUTFLOW_OOTD_TEMPLATE_ID": str(template) if transport == "bridge" else "",
            **({"CUTFLOW_AUTO_SUBMIT_AFTER": cutover} if auto_submit else {}),
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Do not include user-supplied values or a validation repr containing secrets.
        raise SystemExit(
            "Invalid classic CUT configuration; inspect local config/token files."
        ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "worker", "migrate"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    storage = root / "var" / "classic-lan"
    configured = cutflow_environment(storage)
    if args.action == "check":
        print(json.dumps({"configured": bool(configured),
                          "automatic": configured.get("CUTFLOW_AUTO_EDIT_ENABLED") == "true",
                          "autoSubmit": "CUTFLOW_AUTO_SUBMIT_AFTER" in configured}))
        return
    from classic_lan import STATE, configured_lan_ip, lan_environment
    from classic_stack import owned_process

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if args.action == "migrate" and any(
        owned_process(record) for record in state.get("processes", [])
    ):
        raise SystemExit("Stop the classic LAN instance before its authorized schema migration.")
    environment = lan_environment(
        state.get("apiPort", 8002), state.get("webPort", 5176),
        state.get("lanIp") or configured_lan_ip() or "127.0.0.1",
    )
    os.environ.clear()
    os.environ.update(environment)
    if args.action == "migrate":
        sys.path.insert(0, str(root / "backend" / "src"))
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(root / "backend" / "alembic.ini")), "head")
    else:
        if not configured:
            raise SystemExit("Classic CUT is not configured.")
        from classic_worker_lease import exclusive_classic_worker
        from classic_worker_source import worker_source

        release, revision = worker_source("cutflow", storage / "runtime", root)
        sys.path.insert(0, str(release / "backend" / "src"))
        from libtv_agent.runner.classic_checkpoint import CheckpointStop
        from libtv_agent.runner.cutflow_worker import run

        with exclusive_classic_worker("cutflow", storage / "runtime"):
            stopping = CheckpointStop(storage / "runtime", "cutflow", revision)
            asyncio.run(run(once=False, stopping=stopping))


if __name__ == "__main__":
    main()
