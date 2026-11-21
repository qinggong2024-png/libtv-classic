import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from libtv_agent.api.dependencies import Actor, Session
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import AuditEvent, User, UserSession
from libtv_agent.services.account_access import account_manager, platform_admin, visible_users
from libtv_agent.services.page_access import PAGE_PERMISSIONS, PagePermission, page_permissions
from libtv_agent.services.prompt_presets import add_default_prompt_presets
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/admin/users", tags=["admin"])


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=512)
    brandId: uuid.UUID | None = None
    role: UserRole = UserRole.USER
    isBrandAdmin: bool = False
    pagePermissions: list[PagePermission] = Field(default_factory=lambda: list(PAGE_PERMISSIONS))

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip()
        if not username or any(character.isspace() for character in username):
            raise ValueError("username cannot be empty or contain whitespace")
        return username


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=12, max_length=512)


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: UserRole
    isActive: bool
    createdAt: datetime
    brandId: uuid.UUID | None
    isBrandAdmin: bool
    dailyBudgetUsd: float | None
    imagePriceUsd: float | None
    videoPricePerSecondUsd: float | None
    depthVideoPricePerSecondUsd: float | None
    recreateVideoPricePerSecondUsd: float | None
    pagePermissions: list[PagePermission]


def require_account_admin(actor: User) -> None:
    if not account_manager(actor):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrator role is required")


def user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        isActive=user.is_active,
        createdAt=user.created_at,
        brandId=user.brand_id,
        isBrandAdmin=user.is_brand_admin,
        dailyBudgetUsd=None
        if user.daily_budget_microusd is None
        else user.daily_budget_microusd / 1_000_000,
        imagePriceUsd=None
        if user.image_price_microusd is None
        else user.image_price_microusd / 1_000_000,
        videoPricePerSecondUsd=None
        if user.video_price_microusd is None
        else user.video_price_microusd / 1_000_000,
        depthVideoPricePerSecondUsd=None
        if user.depth_video_price_microusd is None
        else user.depth_video_price_microusd / 1_000_000,
        recreateVideoPricePerSecondUsd=None
        if user.recreate_video_price_microusd is None
        else user.recreate_video_price_microusd / 1_000_000,
        pagePermissions=page_permissions(user),
    )


def audit_event(actor_id: uuid.UUID, action: str, user: User, reason: str) -> AuditEvent:
    return AuditEvent(
        actor_id=actor_id,
        action=action,
        entity_type="app_user",
        entity_id=str(user.id),
        reason=reason,
        correlation_id=str(uuid.uuid4()),
        evidence_refs_json=[],
    )


async def get_managed_user(session: Session, user_id: uuid.UUID, actor: User) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.id.in_(visible_users(actor)))
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user account was not found")
    if (
        user.role == UserRole.ADMIN
        or user.id == actor.id
        or (not platform_admin(actor) and user.is_brand_admin)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "PROTECTED_ACCOUNT",
                "message": "administrator accounts cannot be changed here",
            },
        )
    return user


async def revoke_sessions(session: Session, user_id: uuid.UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


@router.get("", response_model=list[AdminUserResponse])
async def list_users(actor: Actor, session: Session) -> list[AdminUserResponse]:
    require_account_admin(actor)
    users = list(
        await session.scalars(
            select(User)
            .where(User.id.in_(visible_users(actor)))
            .order_by(User.created_at, User.username)
        )
    )
    return [user_response(user) for user in users]


@router.post("", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest, actor: Actor, session: Session, request: Request
) -> AdminUserResponse:
    require_account_admin(actor)
    from libtv_agent.domain.models import Brand

    if payload.role == UserRole.ADMIN:
        raise HTTPException(403, "不能通过成员管理创建平台管理员")
    if not platform_admin(actor) and (
        payload.brandId not in {None, actor.brand_id} or payload.isBrandAdmin
    ):
        raise HTTPException(403, "品牌管理员不能更改品牌范围或任命品牌管理员")
    brand_id = payload.brandId if platform_admin(actor) else actor.brand_id
    if payload.isBrandAdmin and brand_id is None:
        raise HTTPException(422, "品牌管理员必须归属品牌")
    if payload.isBrandAdmin and payload.role == UserRole.AUDITOR:
        raise HTTPException(422, "品牌管理员不能为只读账号")
    if brand_id is not None and await session.get(Brand, brand_id) is None:
        raise HTTPException(404, "品牌不存在")
    existing = await session.scalar(select(User.id).where(User.username == payload.username))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "USERNAME_EXISTS", "message": "username already exists"},
        )
    user = User(
        id=uuid.uuid4(),
        username=payload.username,
        password_hash=await request.app.state.password_service.hash(payload.password),
        role=payload.role,
        brand_id=brand_id,
        is_brand_admin=payload.isBrandAdmin,
        is_active=True,
        daily_budget_microusd=5_000_000,
        page_permissions_json=list(dict.fromkeys(payload.pagePermissions)),
    )
    session.add(user)
    try:
        await session.flush()
        add_default_prompt_presets(session, user.id)
        session.add(
            audit_event(actor.id, "CREATE_USER", user, "administrator created user account")
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "USERNAME_EXISTS", "message": "username already exists"},
        ) from exc
    await session.refresh(user)
    return user_response(user)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    actor: Actor,
    session: Session,
    request: Request,
) -> Response:
    require_account_admin(actor)
    user = await get_managed_user(session, user_id, actor)
    if not user.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "ACCOUNT_INACTIVE", "message": "user account is inactive"},
        )
    user.password_hash = await request.app.state.password_service.hash(payload.password)
    await revoke_sessions(session, user.id)
    session.add(
        audit_event(actor.id, "RESET_USER_PASSWORD", user, "administrator reset user password")
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: uuid.UUID, actor: Actor, session: Session) -> Response:
    require_account_admin(actor)
    user = await get_managed_user(session, user_id, actor)
    if user.is_active:
        user.is_active = False
        await revoke_sessions(session, user.id)
        session.add(
            audit_event(actor.id, "DEACTIVATE_USER", user, "administrator deactivated user account")
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
