import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from libtv_agent.db import get_session
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import User, UserSession
from libtv_agent.services.page_access import management_admin, require_any_page
from libtv_agent.services.prompt_presets import add_default_prompt_presets
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

Session = Annotated[AsyncSession, Depends(get_session)]


def apply_actor_scope(user: User, session: AsyncSession, request: Request) -> User:
    session.info["brand_id"] = user.brand_id
    session.info["actor_id"] = user.id
    path = request.url.path
    if path.startswith("/api/v1/admin/") and not management_admin(user):
        raise HTTPException(403, "管理中心仅允许已授权的平台管理员访问")
    if not management_admin(user):
        relative = path.removeprefix("/api/v1")
        if relative.startswith(("/image-generation-batches", "/image-generation-outputs")):
            require_any_page(user, "images")
        elif relative.startswith(("/video-tasks", "/video-templates", "/uploads/video")):
            require_any_page(user, "make")
        elif relative.startswith("/tasks"):
            if request.method in {"GET", "HEAD"} or relative.endswith(
                (
                    "/feedback", "/final-approval", "/cancel",
                    "/restore-remote-output", "/attention-acknowledgement",
                )
            ):
                require_any_page(user, "make", "records")
            else:
                require_any_page(user, "make")
        elif relative.startswith("/cutflow-handoffs"):
            if relative == "/cutflow-handoffs/sites" and request.method in {"GET", "HEAD"}:
                require_any_page(user, "images", "make", "records")
            elif request.method in {"GET", "HEAD"}:
                require_any_page(user, "make", "records")
            else:
                require_any_page(user, "make")
        elif relative.startswith(("/confirmations", "/downloads", "/assets", "/events")):
            require_any_page(user, "make", "records")
            if relative.startswith("/confirmations"):
                require_any_page(user, "make")
        elif relative.startswith(("/motion-actions", "/site-style-profiles", "/library/")):
            if request.method in {"GET", "HEAD"}:
                require_any_page(user, "make")
            else:
                require_any_page(user, "library")
        elif relative.startswith("/uploads"):
            if request.method in {"GET", "HEAD"}:
                require_any_page(user, "images", "make", "records")
            else:
                require_any_page(user, "images", "make")
        elif relative.startswith("/prompt-presets"):
            require_any_page(user, "images", "make")
        elif relative.startswith("/submission-recovery"):
            require_any_page(user, "images", "make", "records")
    if (
        user.role == UserRole.AUDITOR
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not path.startswith("/api/v1/auth/")
    ):
        raise HTTPException(403, "只读账号不能执行写操作")
    return user


async def get_actor(
    session: Session,
    request: Request,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> User:
    settings = request.app.state.settings
    if settings.shared_authentication:
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authenticated session is required")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        user = await session.scalar(
            select(User)
            .join(UserSession, UserSession.user_id == User.id)
            .where(
                UserSession.token_hash == token_hash,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
                User.is_active.is_(True),
            )
        )
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session is invalid or expired")
        return apply_actor_scope(user, session, request)
    if not x_user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "X-User-Id is required in phase-one offline mode"
        )
    try:
        user_id = uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid X-User-Id") from exc
    user = await session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if (
        user is None
        and settings.app_env in {"development", "test"}
        and x_user_id
        in {
            settings.dev_user_id,
            settings.dev_admin_id,
        }
    ):
        role = UserRole.ADMIN if x_user_id == settings.dev_admin_id else UserRole.USER
        user = User(
            id=user_id,
            username="classic-admin" if role == UserRole.ADMIN else "phase-one-user",
            password_hash="offline-disabled",
            role=role,
        )
        session.add(user)
        try:
            await session.flush()
            add_default_prompt_presets(session, user.id)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            user = await session.scalar(
                select(User).where(User.id == user_id, User.is_active.is_(True))
            )
            if user is None:
                raise
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user is not active")
    return apply_actor_scope(user, session, request)


Actor = Annotated[User, Depends(get_actor)]


def require_review_write(actor: User) -> None:
    """Keep auditors read-only while preserving ordinary-owner review behavior."""

    if actor.role is UserRole.AUDITOR:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PERMISSION_DENIED",
                "message": "auditor role is read-only",
            },
        )


def can_review_across_owners(actor: User) -> bool:
    return actor.role in {UserRole.OPERATOR, UserRole.ADMIN} or bool(actor.is_brand_admin)
