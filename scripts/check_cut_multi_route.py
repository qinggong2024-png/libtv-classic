"""Read-only probe: is CUT's multi-source route live, and is our token accepted?

This answers the two questions that cannot be settled by reading local code:

* does the running CUT build actually serve
  ``GET /api/integrations/libtv-multisource/{taskId}``?
* does the token this instance already holds authenticate against that host?

It never creates, submits or mutates anything. Every request is a GET, and the
task identifier is a freshly generated random UUID that cannot exist.

The single-source route is used as a positive control: it is already in
production, so it separates "host down" and "token rejected" from "route
missing". A Next.js catch-all 404 is HTML; a real route answers JSON.

A 5xx is treated as inconclusive rather than healthy: a gateway or upstream
error proves nothing about the route, and counting it as present would produce a
false "ready to open".

Exit codes
    0  route live, token accepted, admission gate open — safe to open the gate
    1  route missing, token rejected, host unreachable or answer inconclusive
    2  local CUT configuration could not be read
    3  route live but CUT admission is still closed (deployed, not yet opened)

Usage
    python scripts/check_cut_multi_route.py
    python scripts/check_cut_multi_route.py --base-url http://127.0.0.1:3001
    python scripts/check_cut_multi_route.py --storage var/classic-lan
"""
from __future__ import annotations

import argparse
import json
import re
import runpy
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGLE = "/api/integrations/libtv/"
MULTI = "/api/integrations/libtv-multisource/"
TIMEOUT = 8.0
MAX_BODY = 4096

# How a response is read. Only APP means "the CUT application itself answered".
UNREACHABLE = "UNREACHABLE"
UPSTREAM = "UPSTREAM"        # non-JSON 5xx: proxy, gateway or crashed upstream
CATCH_ALL = "CATCH_ALL"      # HTML: Next.js fallback, route does not exist
APP = "APP"                  # the application handled the request

# Error codes that mean "the route exists but admission is still closed". A
# correct deployment answers exactly this before the gate is opened, so it must
# not be reported as a transport failure.
CLOSED_MARKERS = (
    "CLOSED_MISSING_TRUSTED_DEPENDENCIES",
    "MULTISOURCE_DISABLED",
    "NOT_READY",
)
SCREAMING = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def load_channel(storage: Path) -> tuple[str, str] | None:
    """Return (base_url, token) from the same config the stack uses."""
    try:
        environment = runpy.run_path(str(ROOT / "scripts" / "classic_cutflow.py"))
        values = environment["cutflow_environment"](storage)
    except (SystemExit, KeyError):
        return None
    base = (values.get("CUTFLOW_BASE_URL") or "").rstrip("/")
    token = (values.get("CUTFLOW_SERVICE_TOKEN") or "")
    if not base or not token:
        return None
    return base, token


def get(base: str, path: str, token: str | None) -> tuple[int, str, str]:
    """One GET. Returns (status, content-type, body). Never mutates."""
    request = urllib.request.Request(base + path, method="GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    request.add_header("Cache-Control", "no-store")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            return response.status, content_type, _text(response.read(MAX_BODY))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), _text(exc.read(MAX_BODY))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return 0, "", f"{type(exc).__name__}: {exc}"


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").replace("\n", " ")


def brief(body: str, limit: int = 160) -> str:
    return body if len(body) <= limit else body[:limit] + "…"


def is_html(content_type: str, body: str) -> bool:
    return "html" in content_type.lower() or body.lstrip().startswith("<!DOCTYPE")


def is_app_json(content_type: str, body: str) -> bool:
    """True when the body is an application-shaped JSON error payload.

    A proxy can return JSON too, so the payload must look like this application:
    a SCREAMING_SNAKE `error` code, or a task/status envelope.
    """
    if "json" not in content_type.lower() and not body.lstrip().startswith("{"):
        return False
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if isinstance(error, str) and SCREAMING.match(error):
        return True
    return any(key in payload for key in ("status", "taskId", "contractVersion"))


def classify(status: int, content_type: str, body: str) -> str:
    if status == 0:
        return UNREACHABLE
    if is_html(content_type, body):
        return CATCH_ALL
    if is_app_json(content_type, body):
        # A JSON 5xx is the application answering (usually "gate closed"), not a
        # gateway failure. Misreading it as UPSTREAM would make "deployed but
        # closed" indistinguishable from "not deployed".
        return APP
    if 500 <= status < 600:
        return UPSTREAM
    return APP


def gate_closed(status: int, body: str) -> bool:
    """Admission is closed when the app refuses work or says so.

    Any 5xx counts: even if it reached us as application JSON, a server error
    means this route cannot accept a task right now, so it must never be
    reported as ready to open.
    """
    if status >= 500:
        return True
    return any(marker in body for marker in CLOSED_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="override the configured CUT base URL")
    parser.add_argument("--storage", default="var/classic-lan", help="classic stack storage dir")
    args = parser.parse_args()

    storage = (ROOT / args.storage) if not Path(args.storage).is_absolute() else Path(args.storage)
    channel = load_channel(storage)
    if channel is None:
        print("FAIL 本机 CUT 通道配置不可读（cutflow-config.json 或 token 缺失）")
        return 2
    base, token = channel
    if args.base_url:
        base = args.base_url.rstrip("/")
    print(f"目标           {base}")
    print(f"token          已读取，长度 {len(token)}（不打印值）")
    probe = str(uuid.uuid4())

    root_status, root_type, root_body = get(base, "/", None)
    # The site root is the HTML app page, so HTML here is normal; only a
    # transport failure or a 5xx means the application is not answering.
    reachable = root_status != 0 and root_status < 500
    detail = f"  {brief(root_body)}" if not reachable else ""
    print(f"可达性         {'OK' if reachable else 'FAIL'}  GET / -> {root_status}{detail}")
    if not reachable:
        print("\n结论           不能开闸：CUT 应用本身没有应答（不可达或上游 5xx）")
        return 1

    control_status, control_type, control_body = get(base, SINGLE + probe, None)
    control_kind = classify(control_status, control_type, control_body)
    print(f"正对照(单源)   {'OK' if control_kind == APP else 'FAIL'}  "
          f"{SINGLE}{{uuid}} 不带凭据 -> {control_status}  {brief(control_body)}")
    if control_kind != APP:
        print("\n结论           不可判定：单源路由也未由应用应答，这个地址可能不是 CUT 应用")
        return 1

    authed_status, authed_type, authed_body = get(base, SINGLE + probe, token)
    authed_kind = classify(authed_status, authed_type, authed_body)
    token_ok = authed_kind == APP and authed_status != 401
    print(f"token 有效性   {'OK' if token_ok else 'FAIL'}  同路径带凭据 -> "
          f"{authed_status}  {brief(authed_body)}")

    multi_status, multi_type, multi_body = get(base, MULTI + probe, token)
    multi_kind = classify(multi_status, multi_type, multi_body)
    multi_ok = multi_kind == APP
    shown = {
        CATCH_ALL: "HTML 兜底页（路由不存在）",
        UPSTREAM: "上游 5xx（无法判定）",
        UNREACHABLE: multi_body,
    }.get(multi_kind, multi_body)
    print(f"多源路由       {'OK' if multi_ok else 'MISSING'}  {MULTI}{{uuid}} -> "
          f"{multi_status}  {brief(shown)}")

    closed = multi_ok and gate_closed(multi_status, multi_body)
    if multi_ok:
        print(f"收单门禁       {'CLOSED' if closed else 'OPEN'}  "
              f"{'路由已上线但服务仍 fail-closed' if closed else '路由已可接单'}")
    print()

    if multi_ok and token_ok and not closed:
        print("结论           可以开闸：多源路由在线、token 被接受、门禁已开")
        return 0
    if multi_ok and closed:
        print(f"结论           路由已应答但拒绝接单（HTTP {multi_status}）：")
        print("               CUT 侧门禁或生产依赖未就绪，经典版暂不能开闸")
        return 3
    reasons = []
    if not token_ok:
        reasons.append("token 未被接受或无法判定")
    if multi_kind == CATCH_ALL:
        reasons.append("CUT 活动构建未包含多源路由（需先部署）")
    elif multi_kind == UPSTREAM:
        reasons.append("多源路由应答为上游 5xx，无法判定是否就绪")
    elif not multi_ok:
        reasons.append("多源路由应答无法判定")
    print("结论           不能开闸：" + "；".join(reasons))
    print("               开闸会表现为任务停在 QUEUED 并以 CUTFLOW_HTTP_ERROR 反复重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())
