"""Pick only terminal, technically admissible originals for automatic CUT.

One failed provider segment must not strand finished originals. Never skip a
segment that may still finish, download, or pass technical validation.
"""
from __future__ import annotations

from libtv_agent.domain.enums import (
    DownloadStatus, GenerationStatus, ParentTaskPhase, ValidationStatus,
)

FINAL_PARENT_PHASES = {
    ParentTaskPhase.ACTION_REQUIRED,
    ParentTaskPhase.PARTIALLY_DELIVERABLE,
    ParentTaskPhase.DELIVERABLE,
}


def source_ready(variant) -> bool:
    return (
        variant.generation_status in (GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL)
        and variant.download_status == DownloadStatus.READY
        and variant.validation_status in (ValidationStatus.PASSED, ValidationStatus.WARNING)
    )


def eligible_auto_variants(variants: list, phase: ParentTaskPhase) -> list | None:
    """None means wait; [] means terminal with no usable source.

    A nonempty subset is permitted only after the parent ends and every
    excluded source has an explicit terminal failure.  Unknown provider and
    still-downloading states remain waiting, not silent exclusions.
    """
    if not 1 <= len(variants) <= 8 or phase == ParentTaskPhase.CANCELLED:
        return None
    ready = [variant for variant in variants if source_ready(variant)]
    if len(ready) == len(variants):
        return ready
    if phase not in FINAL_PARENT_PHASES:
        return None
    for variant in variants:
        if source_ready(variant):
            continue
        if variant.generation_status == GenerationStatus.FAILED:
            continue
        if (variant.generation_status in (GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL)
            and (variant.validation_status == ValidationStatus.FAILED
                 or variant.download_status == DownloadStatus.FAILED)):
            continue
        return None
    return ready
