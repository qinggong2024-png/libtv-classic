"""Print-agnostic, bounded, persistent director memory for classic ALLWE OOTD.

A print changes every task; it is NEVER a memory key. Only reusable brand
voice, wearer/garment-category presentation, reviewed directing outcomes and
provider execution risks belong here. The original image is always the sole
authority for the current garment graphic and visible product facts.

Runtime modes: off (default), shadow (read/trace only), on (apply).
This module never calls providers or edits existing task state.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_LOG = logging.getLogger(__name__)
SCHEMA = "allwe-director-memory-v1"
MAX_BYTES = 48_000
MAX_RULES = 64
MAX_CONTEXT_CHARS = 420
MAX_SELECTED_RULES = 3
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "knowledge" / "ootd_director_memory_v1.json"
_BRAND_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{1,78}[a-z0-9]")
_ALLOWED_CATEGORIES = {"tee", "sweatshirt", "sweater", "hoodie", "scarf", "other"}
_ALLOWED_MODES = {"cross_scene_v2", "reference_guided"}
_BLOCKED_FRAGMENTS = ("@image", "http://", "https://", "<", ">", "\r", "\n", "ignore previous",
                      "ignore all", "system prompt", "忽略前", "忽略以上")


class MemoryKind(StrEnum):
    BRAND = "BRAND"
    FIT_MATERIAL = "FIT_MATERIAL"
    DIRECTOR_PATTERN = "DIRECTOR_PATTERN"
    EXECUTION_RISK = "EXECUTION_RISK"


class MemoryDestination(StrEnum):
    SOL = "SOL"
    KLING = "KLING"


class MemoryState(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class MemoryRule:
    rule_id: str
    revision: int
    state: MemoryState
    kind: MemoryKind
    destination: MemoryDestination
    brand: str
    text: str
    origin: str
    evidence_ids: tuple[str, ...]
    garment_category: str | None = None
    director_mode: str | None = None
    priority: int = 0
    reviewed_by: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{2,79}", self.rule_id):
            raise ValueError("invalid memory ID")
        if (self.revision < 1 or not isinstance(self.brand, str)
                or not _BRAND_ID.fullmatch(self.brand)):
            raise ValueError("invalid memory identity")
        if self.garment_category is not None and self.garment_category not in _ALLOWED_CATEGORIES:
            raise ValueError("unsupported garment category")
        if self.director_mode is not None and self.director_mode not in _ALLOWED_MODES:
            raise ValueError("unsupported director mode")
        if not 12 <= len(self.text.strip()) <= 210 or any(
            token in self.text.casefold() for token in _BLOCKED_FRAGMENTS
        ):
            raise ValueError("memory must be short safe category-level guidance")
        if not -100 <= self.priority <= 100:
            raise ValueError("invalid priority")
        if self.kind in {MemoryKind.BRAND, MemoryKind.DIRECTOR_PATTERN}:
            if self.destination != MemoryDestination.SOL:
                raise ValueError("creative direction is Sol-only")
        if self.kind == MemoryKind.EXECUTION_RISK and self.destination != MemoryDestination.KLING:
            raise ValueError("execution guard is Kling-only")
        if self.origin not in {"CURATED", "REVIEWED_VIDEO_OUTCOME"}:
            raise ValueError("untrusted memory provenance")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("memory needs unique provenance references")
        if self.origin == "REVIEWED_VIDEO_OUTCOME":
            if not self.reviewed_by or not re.fullmatch(r"[a-zA-Z0-9_.:-]{3,80}", self.reviewed_by):
                raise ValueError("video-learned memory needs a named human reviewer")
            if (self.state == MemoryState.ACTIVE and len(self.evidence_ids) < 2) or any(
                not re.fullmatch(r"[0-9a-f]{64}", ref) for ref in self.evidence_ids
            ):
                raise ValueError("active video memory needs two distinct video SHA256s")
        if self.kind in {MemoryKind.DIRECTOR_PATTERN, MemoryKind.EXECUTION_RISK}:
            if self.origin != "REVIEWED_VIDEO_OUTCOME":
                raise ValueError("learned director/risk guidance needs verified outcomes")

    @classmethod
    def from_dict(cls, data: dict) -> MemoryRule:
        return cls(
            rule_id=data["rule_id"],
            revision=data["revision"],
            state=MemoryState(data["state"]),
            kind=MemoryKind(data["kind"]),
            destination=MemoryDestination(data["destination"]),
            brand=data["brand"],
            text=data["text"],
            origin=data["origin"],
            evidence_ids=tuple(data["evidence_ids"]),
            garment_category=data.get("garment_category"),
            director_mode=data.get("director_mode"),
            priority=data.get("priority", 0),
            reviewed_by=data.get("reviewed_by"),
        )

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "revision": self.revision,
            "state": self.state.value,
            "kind": self.kind.value,
            "destination": self.destination.value,
            "brand": self.brand,
            "text": self.text,
            "origin": self.origin,
            "evidence_ids": list(self.evidence_ids),
            "garment_category": self.garment_category,
            "director_mode": self.director_mode,
            "priority": self.priority,
            "reviewed_by": self.reviewed_by,
        }


@dataclass(frozen=True)
class SelectedMemory:
    rule_ids: tuple[str, ...]
    revisions: tuple[int, ...]
    hints: tuple[str, ...]
    total_chars: int
    mode: str

    @property
    def applicable(self) -> bool:
        return bool(self.hints)

    @property
    def applied(self) -> bool:
        return self.mode == "on" and self.applicable


def memory_mode() -> str:
    """Explicit experimental opt-in, independent of existing director flags."""
    mode = os.getenv("ALLWE_DIRECTOR_MEMORY_MODE", "off").strip().casefold()
    return mode if mode in {"off", "shadow", "on"} else "off"


def memory_path() -> Path:
    raw = os.getenv("ALLWE_DIRECTOR_MEMORY_FILE", "").strip()
    return Path(raw) if raw else DEFAULT_PATH


def _latest_only(rules: tuple[MemoryRule, ...]) -> tuple[MemoryRule, ...]:
    grouped: dict[str, list[MemoryRule]] = {}
    for rule in rules:
        previous = grouped.get(rule.rule_id, [])
        if not previous or rule.revision > previous[0].revision:
            grouped[rule.rule_id] = [rule]
        elif rule.revision == previous[0].revision:
            previous.append(rule)
    # An identical ID with a conflicting top version is never applied.
    return tuple(
        versions[0] for versions in grouped.values()
        if len(set(versions)) == 1
    )


def load_memory_rules(path: Path) -> tuple[MemoryRule, ...]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("memory file exceeds size limit")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ValueError("unknown memory schema")
    items = raw.get("rules")
    if not isinstance(items, list) or len(items) > MAX_RULES:
        raise ValueError("invalid memory rule count")
    return tuple(MemoryRule.from_dict(item) for item in items)


def append_memory_revision(path: Path, rule: MemoryRule) -> None:
    """Explicit offline operator action. Never called by generation workers.

    Atomic durable JSON revision with a nonblocking exclusive writer lock.
    Production readers need no lock; they see old or new complete content.
    A tombstone prevents a prior version from being revived by a later write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("untrusted memory path")
    lock_path = path.with_name(path.name + ".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(lock_fd)
        try:
            history = load_memory_rules(path)
        except FileNotFoundError:
            history = ()
        if len(history) >= MAX_RULES:
            raise ValueError("memory history is full")
        existing = tuple(item for item in history if item.rule_id == rule.rule_id)
        if existing:
            latest = max(existing, key=lambda item: item.revision)
            if latest.state == MemoryState.REVOKED:
                raise ValueError("revoked memory ID is permanently tombstoned")
            if rule.revision != latest.revision + 1:
                raise ValueError("memory revision must increase by one")
            if (rule.brand, rule.destination, rule.kind) != (
                latest.brand, latest.destination, latest.kind
            ):
                raise ValueError("memory rule identity cannot change scope")
        elif rule.revision != 1:
            raise ValueError("new memory starts at revision one")
        doc = {"schema": SCHEMA, "rules": [*(r.to_dict() for r in history), rule.to_dict()]}
        rendered = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if len(rendered.encode("utf-8")) > MAX_BYTES:
            raise ValueError("memory exceeds file size cap")
        fd, tmp = tempfile.mkstemp(prefix=".allwe-memory-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(rendered)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def select_memory(
    rules: tuple[MemoryRule, ...], *,
    brand: str,
    destination: MemoryDestination,
    director_mode: str,
    garment_category: str | None = None,
    max_rules: int = MAX_SELECTED_RULES,
    max_chars: int = MAX_CONTEXT_CHARS,
    mode: str = "on",
) -> SelectedMemory:
    if max_rules < 0 or max_chars < 0:
        raise ValueError("invalid memory context limits")
    if mode not in {"off", "shadow", "on"}:
        raise ValueError("invalid memory mode")
    if mode == "off" or not isinstance(brand, str) or not _BRAND_ID.fullmatch(brand):
        return SelectedMemory((), (), (), 0, mode)
    eligible = [
        item for item in _latest_only(rules)
        if item.state == MemoryState.ACTIVE
        and item.brand == brand
        and item.destination == destination
        and (item.director_mode is None or item.director_mode == director_mode)
        and (item.garment_category is None or item.garment_category == garment_category)
    ]
    eligible.sort(
        key=lambda r: (
            -int(r.garment_category is not None),
            -int(r.director_mode is not None), -r.priority, r.rule_id,
        )
    )
    selected: list[MemoryRule] = []
    total = 0
    for rule in eligible:
        if len(selected) >= max_rules:
            break
        if total + len(rule.text) > max_chars:
            continue
        selected.append(rule)
        total += len(rule.text)
    return SelectedMemory(
        tuple(r.rule_id for r in selected),
        tuple(r.revision for r in selected),
        tuple(r.text for r in selected), total, mode,
    )


def runtime_memory(
    *, brand: str,
    destination: MemoryDestination,
    director_mode: str,
    garment_category: str | None = None,
) -> SelectedMemory:
    """Fast, fail-open file read; only caller decides whether to apply hints."""
    mode = memory_mode()
    if mode == "off":
        return SelectedMemory((), (), (), 0, mode)
    try:
        selection = select_memory(
            load_memory_rules(memory_path()), brand=brand,
            destination=destination, director_mode=director_mode,
            garment_category=garment_category, mode=mode,
        )
        _LOG.info(
            "DIRECTOR_MEMORY_SELECTION mode=%s target=%s brand=%s rules=%s",
            mode, destination.value, brand, list(selection.rule_ids),
        )
        return selection
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        _LOG.warning("DIRECTOR_MEMORY_FALLBACK: %s", type(exc).__name__)
        return SelectedMemory((), (), (), 0, mode)


def memory_hint_text(selection: SelectedMemory, *, destination: MemoryDestination) -> str:
    if not selection.applied:
        return ""
    if destination == MemoryDestination.SOL:
        prefix = "可复用导演经验（仅影响风格和行为，不是当前商品事实）："
    else:
        prefix = "可复用执行经验（不改变商品原图和 Sol 已定镜头）："
    return prefix + " ".join(selection.hints)
