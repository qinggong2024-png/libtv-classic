"""Bounded process-local telemetry: no business DB writes, SQL text or user data."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from contextvars import ContextVar
from threading import RLock
from weakref import WeakKeyDictionary

from sqlalchemy import event

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 900
MAX_KEYS = 192
MAX_SAMPLES = 256
_scope: ContextVar[dict | None] = ContextVar("performance_http_scope", default=None)
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_WRITE = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}
_STATEMENTS = _WRITE | {"SELECT", "WITH", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK"}


def route_key(scope=None):
    scope = scope if scope is not None else _scope.get()
    if scope is None:
        return "background"
    route = getattr(scope.get("route"), "path", None)
    method = scope.get("method")
    return f"{method if method in _METHODS else 'OTHER'} {route or 'unmatched'}"


class PerformanceObservation:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.lock = RLock()
        self.rows = {}
        self.last_log = {}
        self.active = WeakKeyDictionary()

    def record(self, kind, key, elapsed_ms, *, failed=False, locked=False, slow_ms=None):
        now = self.clock()
        elapsed_ms = max(0.0, elapsed_ms)
        with self.lock:
            group = (kind, key)
            if group not in self.rows and len(self.rows) >= MAX_KEYS:
                group = (kind, "other")
            if group not in self.rows and len(self.rows) >= MAX_KEYS + 8:
                return
            row = self.rows.setdefault(group, [0, 0, 0, deque(maxlen=MAX_SAMPLES)])
            row[0] += 1
            row[1] += int(failed)
            row[2] += int(locked)
            row[3].append((now, elapsed_ms))
            if (locked or (slow_ms is not None and elapsed_ms >= slow_ms)):
                if now - self.last_log.get(group, -math.inf) >= 10:
                    self.last_log[group] = now
                    logger.warning(
                        "performance kind=%s key=%s duration_ms=%.1f locked=%s",
                        *group, elapsed_ms, locked,
                    )

    def snapshot(self):
        now = self.clock()
        with self.lock:
            rows = []
            for (kind, key), (count, errors, locks, samples) in self.rows.items():
                values = sorted(value for stamp, value in samples if now - stamp <= WINDOW_SECONDS)
                if not values:
                    continue
                rows.append({
                    "kind": kind, "key": key, "totalSinceStart": count,
                    "errorsSinceStart": errors, "locksSinceStart": locks,
                    "sampleCount": len(values),
                    "p95Ms": round(values[math.ceil(len(values) * .95) - 1], 1),
                    "maxMs": round(values[-1], 1),
                })
            active = [
                {"key": row[1], "ageMs": round((now - row[0]) * 1000, 1),
                 "writePhaseMs": round((now - row[2]) * 1000, 1) if row[2] is not None else None}
                for row in self.active.values()
            ]
        return {
            "scope": "current-api-process", "windowSeconds": WINDOW_SECONDS,
            "sampleLimitPerKey": MAX_SAMPLES, "uptimeSeconds": round(now - self.started),
            "rows": sorted(rows, key=lambda row: (row["kind"], -row["p95Ms"])),
            "activeTransactions": sorted(active, key=lambda row: -row["ageMs"])[:20],
        }


observation = PerformanceObservation()


class PerformanceMiddleware:
    """Measure time to response headers, not playback or SSE connection duration."""

    def __init__(self, app, observer=observation):
        self.app = app
        self.observer = observer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _scope.set(scope)
        start = self.observer.clock()
        recorded = False

        async def measured_send(message):
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                self.observer.record(
                    "http_ttfb", route_key(scope), (self.observer.clock() - start) * 1000,
                    failed=message["status"] >= 500, slow_ms=500,
                )
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        finally:
            if not recorded:
                self.observer.record(
                    "http_ttfb", route_key(scope), (self.observer.clock() - start) * 1000,
                    failed=True, slow_ms=500,
                )
            _scope.reset(token)


def install_database_observation(engine, observer=observation):
    """Observe engine transactions until commit/rollback is requested.

    Write phase starts before first write SQL (includes lock acquisition wait);
    it is not a measurement of exact lock ownership or commit completion.
    """
    @event.listens_for(engine, "begin")
    def begin(conn):
        with observer.lock:
            observer.active[conn] = [observer.clock(), route_key(), None]

    @event.listens_for(engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        del cursor, parameters, executemany
        parts = statement.lstrip().split(None, 1)
        verb = parts[0].upper() if parts else ""
        context._classic_perf = (observer.clock(), verb if verb in _STATEMENTS else "OTHER")
        if verb in _WRITE:
            with observer.lock:
                row = observer.active.get(conn)
                if row is not None and row[2] is None:
                    row[2] = observer.clock()

    def sql_done(context, *, failed=False, locked=False):
        started = getattr(context, "_classic_perf", None)
        if started is not None:
            observer.record(
                "sql", f"{route_key()} {started[1]}",
                (observer.clock() - started[0]) * 1000,
                failed=failed, locked=locked, slow_ms=200,
            )

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        sql_done(context)

    @event.listens_for(engine, "handle_error")
    def error(context):
        # Only classify the original exception; never log statement or values.
        original = context.original_exception
        code = getattr(original, "sqlite_errorcode", None)
        locked = isinstance(code, int) and (code & 255) in {5, 6}
        if not locked and engine.dialect.name == "sqlite":
            message = str(original).lower()
            locked = "database is locked" in message or "database table is locked" in message
        sql_done(context.execution_context, failed=True, locked=locked)
        if locked and not getattr(context.execution_context, "_classic_perf", None):
            observer.record("sql", f"{route_key()} OTHER", 0, failed=True, locked=True)

    def finish(conn):
        with observer.lock:
            row = observer.active.pop(conn, None)
        if row is None:
            return
        now = observer.clock()
        observer.record("transaction", row[1], (now - row[0]) * 1000, slow_ms=2000)
        if row[2] is not None:
            observer.record("write_phase", row[1], (now - row[2]) * 1000, slow_ms=300)

    event.listen(engine, "commit", finish)
    event.listen(engine, "rollback", finish)
