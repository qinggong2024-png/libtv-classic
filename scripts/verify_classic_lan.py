"""Verify only an explicitly named classic LAN instance, with no media generation."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.port == 5173 or target.hostname not in {"127.0.0.1", "192.168.30.126"}:
        raise SystemExit("Verification is limited to the independent classic instance.")
    credentials = json.loads(
        (ROOT / "var" / "classic-lan" / "runtime" / "initial-admin.json").read_text(
            encoding="utf-8"
        )
    )
    checks = []
    with httpx.Client(base_url=args.url, trust_env=False, timeout=15) as client:
        home = client.get("/")
        home.raise_for_status()
        assert "/@vite/client" not in home.text
        assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', home.text)
        assert assets
        for asset in assets:
            client.get(asset).raise_for_status()
        checks.append("static production build and assets")
        status = client.get("/api/v1/system/status").json()
        assert status["sourceRevision"] == "0e3d1c831efd4cd074654a03e36c472f15565cc3"
        assert status["sharedAuthentication"] is True
        assert status["paidGenerationEnabled"] is False
        assert status["paidGenerationAuthorized"] is False
        checks.append("classic identity, session authentication and paid gate")
        forged = {"X-User-Id": "00000000-0000-0000-0000-000000000099"}
        assert client.get("/api/v1/auth/me", headers=forged).status_code == 401
        assert client.get("/api/v1/tasks", headers=forged).status_code == 401
        checks.append("anonymous and forged actor access rejected")
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": credentials["username"], "password": "invalid-test-password"},
            ).status_code
            == 401
        )
        login = client.post("/api/v1/auth/login", json=credentials)
        login.raise_for_status()
        assert client.get("/api/v1/auth/me").json()["username"] == credentials["username"]
        assert client.get("/api/v1/auth/me").json()["role"] == "ADMIN"
        assert "HttpOnly" in login.headers.get("set-cookie", "")
        checks.append("password login and HttpOnly session")
        endpoint = "/api/v1/auth/preferences/prompt-confirmation"
        assert client.patch(endpoint, json={"automatic": False}).status_code == 403
        csrf = {"X-CSRF-Token": login.json()["csrfToken"]}
        client.patch(endpoint, json={"automatic": False}, headers=csrf).raise_for_status()
        checks.append("CSRF rejection and authenticated local preference request")
        assert client.get("/api/v1/tasks").status_code == 200
        client.post("/api/v1/auth/logout", headers=csrf).raise_for_status()
        assert client.get("/api/v1/auth/me").status_code == 401
        checks.append("authenticated task reads and logout")
    print(json.dumps({"url": args.url, "result": "PASS", "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
