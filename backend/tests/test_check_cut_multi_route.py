"""The multi-source route probe must distinguish "route missing" from "ready".

A probe that always answers MISSING is worthless, and one that counts a 5xx as
healthy produces a false "ready to open" — both branches are pinned here against
a local server with three modes: route served as JSON, catch-all HTML only, and
a gateway 5xx.
"""
from __future__ import annotations

import json
import runpy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SCRIPT = "scripts/check_cut_multi_route.py"
SINGLE = "/api/integrations/libtv/"
MULTI = "/api/integrations/libtv-multisource/"
TOKEN = "probe-token-for-tests"


def _storage(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "cutflow-config.json").write_text(json.dumps({
        "version": 1, "enabled": True, "transport": "lan",
        "baseUrl": "http://127.0.0.1:1", "handoffRoot": str(tmp_path / "handoff"),
        "autoSubmitAfter": None,
    }), encoding="utf-8")
    (runtime / "cutflow-service-token.txt").write_text(TOKEN, encoding="utf-8")
    return tmp_path


def _serve(mode: str):
    """mode: 'ready' | 'missing' | 'upstream'."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _send(self, code, content_type, body):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _html_404(self):
            self._send(404, "text/html; charset=utf-8",
                       "<!DOCTYPE html><html><body>Not Found</body></html>")

        def do_GET(self):
            if mode == "upstream":
                self._send(502, "text/plain", "upstream connect failed")
                return
            authorised = self.headers.get("Authorization") == f"Bearer {TOKEN}"
            if self.path == "/":
                # The real host serves its HTML app shell here; that is normal.
                self._send(200, "text/html; charset=utf-8",
                           "<!DOCTYPE html><html><body>CUT</body></html>")
                return
            if self.path.startswith(SINGLE):
                if not authorised:
                    self._send(401, "application/json", '{"error":"UNAUTHORIZED"}')
                else:
                    self._send(404, "application/json", '{"error":"IMPORT_NOT_FOUND"}')
                return
            if self.path.startswith(MULTI):
                if mode == "closed":
                    self._send(503, "application/json",
                               '{"error":"CLOSED_MISSING_TRUSTED_DEPENDENCIES"}')
                    return
                if mode != "ready":
                    self._html_404()
                elif not authorised:
                    self._send(401, "application/json", '{"error":"UNAUTHORIZED"}')
                else:
                    self._send(404, "application/json", '{"error":"TASK_NOT_FOUND"}')
                return
            self._html_404()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(tmp_path, monkeypatch, base_url, capsys):
    module = runpy.run_path(SCRIPT)
    monkeypatch.setattr(
        "sys.argv",
        ["check_cut_multi_route.py", "--base-url", base_url, "--storage", str(tmp_path)],
    )
    try:
        code = module["main"]()
    except SystemExit as exc:  # pragma: no cover - main returns, never exits
        code = exc.code
    return code, capsys.readouterr().out


def _with_server(mode, tmp_path, monkeypatch, capsys):
    server = _serve(mode)
    try:
        return _run(_storage(tmp_path), monkeypatch,
                    f"http://127.0.0.1:{server.server_port}", capsys)
    finally:
        server.shutdown()
        server.server_close()


def test_reports_ready_when_multi_route_answers_json(tmp_path, monkeypatch, capsys):
    code, out = _with_server("ready", tmp_path, monkeypatch, capsys)
    assert code == 0, out
    assert "可达性         OK" in out
    assert "多源路由       OK" in out
    assert "可以开闸" in out


def test_reports_missing_when_only_catch_all_html_404(tmp_path, monkeypatch, capsys):
    code, out = _with_server("missing", tmp_path, monkeypatch, capsys)
    assert code == 1, out
    assert "可达性         OK" in out
    assert "多源路由       MISSING" in out
    assert "未包含多源路由" in out
    # The positive control must still pass, so the verdict is about the route,
    # not about reachability or a rejected token.
    assert "正对照(单源)   OK" in out
    assert "token 有效性   OK" in out


def test_gateway_5xx_is_inconclusive_and_never_claims_ready(tmp_path, monkeypatch, capsys):
    """A 502 is not evidence that the route exists."""
    code, out = _with_server("upstream", tmp_path, monkeypatch, capsys)
    assert code == 1, out
    assert "可以开闸" not in out
    assert "可达性         FAIL" in out
    assert "上游 5xx" in out


def test_deployed_but_closed_is_reported_separately(tmp_path, monkeypatch, capsys):
    """A JSON 503 is the app answering "not open yet", not a transport failure.

    This is exactly the state right after CUT deploys the route, so it must be
    distinguishable from "route missing" without another round of messages.
    """
    code, out = _with_server("closed", tmp_path, monkeypatch, capsys)
    assert code == 3, out
    assert "多源路由       OK" in out
    assert "收单门禁       CLOSED" in out
    assert "拒绝接单" in out
    assert "可以开闸" not in out


def test_unreachable_host_never_claims_ready(tmp_path, monkeypatch, capsys):
    code, out = _run(_storage(tmp_path), monkeypatch, "http://127.0.0.1:1", capsys)
    assert code == 1, out
    assert "可以开闸" not in out


def test_missing_config_is_reported_as_unknown_state(tmp_path, monkeypatch, capsys):
    code, out = _run(tmp_path, monkeypatch, "http://127.0.0.1:1", capsys)
    assert code == 2, out
    assert "配置不可读" in out


@pytest.mark.parametrize("content_type,body,expected_kind", [
    ("application/json", '{"error":"X"}', "APP"),
    ("text/html; charset=utf-8", "<!DOCTYPE html><html></html>", "CATCH_ALL"),
    ("", "<!DOCTYPE html><html></html>", "CATCH_ALL"),
    ("text/plain", '{"error":"X"}', "APP"),
    ("application/json", '{"error":"X"}', "APP"),
])
def test_response_classification(content_type, body, expected_kind):
    module = runpy.run_path(SCRIPT)
    assert module["classify"](404, content_type, body) == expected_kind


@pytest.mark.parametrize(("status", "expected_kind"), [
    (0, "UNREACHABLE"), (502, "UPSTREAM"), (503, "UPSTREAM"), (500, "UPSTREAM"),
])
def test_transport_failures_are_not_treated_as_answers(status, expected_kind):
    module = runpy.run_path(SCRIPT)
    assert module["classify"](status, "application/json", '{"error":"X"}') == expected_kind


@pytest.mark.parametrize(("status", "content_type", "body", "expected_kind"), [
    (503, "application/json", '{"error":"CLOSED_MISSING_TRUSTED_DEPENDENCIES"}', "APP"),
    (502, "text/plain", "upstream connect failed", "UPSTREAM"),
    (502, "text/html; charset=utf-8", "<!DOCTYPE html>", "CATCH_ALL"),
    (500, "application/json", '{"error":"BAD_GATEWAY"}', "APP"),
    (200, "application/json", '{"status":"editing"}', "APP"),
])
def test_application_json_5xx_is_an_answer_not_a_gateway(status, content_type, body,
                                                         expected_kind):
    module = runpy.run_path(SCRIPT)
    assert module["classify"](status, content_type, body) == expected_kind


@pytest.mark.parametrize(("status", "body", "expected"), [
    (503, '{"error":"CLOSED_MISSING_TRUSTED_DEPENDENCIES"}', True),
    (502, "upstream connect failed", True),
    (404, '{"error":"TASK_NOT_FOUND"}', False),
    (200, '{"status":"editing"}', False),
])
def test_gate_closed_never_allows_a_5xx_to_look_ready(status, body, expected):
    module = runpy.run_path(SCRIPT)
    assert module["gate_closed"](status, body) is expected
