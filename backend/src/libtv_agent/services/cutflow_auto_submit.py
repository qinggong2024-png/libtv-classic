"""Discover newly created completed video tasks without a source human-review gate."""

from libtv_agent.config import Settings
from libtv_agent.domain.enums import DownloadStatus, GenerationStatus, UserRole, ValidationStatus
from libtv_agent.domain.models import (
    Asset,
    CutflowHandoff,
    DownloadArtifact,
    GenerationAttempt,
    GenerationVariant,
    ParentTask,
    User,
)
from libtv_agent.services.cutflow_handoff import CutflowHandoffError, create_cutflow_handoff
from libtv_agent.services.page_access import page_permissions
from sqlalchemy import exists, select


async def enqueue_completed(database, settings: Settings, object_store) -> int:
    """An explicit cutover excludes old tasks; existing unique identity handles replay."""
    if not settings.cutflow_auto_edit_enabled or settings.cutflow_auto_submit_after is None:
        return 0
    added = 0
    async with database.session_factory() as session:
        statement = (
            select(ParentTask.user_id, ParentTask.id, GenerationVariant.id).distinct()
            .join(GenerationVariant, GenerationVariant.parent_task_id == ParentTask.id)
            .join(GenerationAttempt, GenerationAttempt.variant_id == GenerationVariant.id)
            .join(DownloadArtifact, DownloadArtifact.attempt_id == GenerationAttempt.id)
            .join(User, User.id == ParentTask.user_id)
            .where(
                ParentTask.created_at >= settings.cutflow_auto_submit_after,
                User.is_active.is_(True),
                User.role != UserRole.AUDITOR,
                GenerationVariant.generation_status.in_(
                    [GenerationStatus.SUCCEEDED, GenerationStatus.PARTIAL]
                ),
                GenerationVariant.validation_status.in_(
                    [ValidationStatus.PASSED, ValidationStatus.WARNING]
                ),
                DownloadArtifact.status == DownloadStatus.READY,
                DownloadArtifact.deleted_at.is_(None),
                DownloadArtifact.deletion_pending_at.is_(None),
                ~exists().where(
                    CutflowHandoff.artifact_id == DownloadArtifact.id,
                    CutflowHandoff.user_id == ParentTask.user_id,
                ),
                exists().where(
                    Asset.parent_task_id == ParentTask.id,
                    Asset.role == "PRODUCT", Asset.deleted_at.is_(None),
                ),
            ).order_by(ParentTask.created_at, ParentTask.id, GenerationVariant.id)
            .execution_options(yield_per=100)
        )
        # Stream all eligible identities so invalid earlier candidates cannot starve later ones.
        candidates = await session.stream(statement)
        async for owner_id, task_id, variant_id in candidates:
            task = await session.get(ParentTask, task_id)
            # Missing legacy setting preserves the old auto-submit behavior.
            # Explicit opt-out is durable across worker restarts and retries.
            if task is None or (task.settings_json or {}).get("autoSubmitToCutflow") is False:
                continue
            actor = await session.get(User, owner_id)
            if actor is None or "make" not in page_permissions(actor):
                continue
            try:
                async with session.begin_nested():
                    await create_cutflow_handoff(
                        session, settings=settings, object_store=object_store,
                        user_id=owner_id, task_id=task_id, variant_id=variant_id,
                    )
            except CutflowHandoffError:
                # Invalid inputs remain unsubmitted; source generation/review is never changed.
                continue
            added += 1
        await session.commit()
    return added
