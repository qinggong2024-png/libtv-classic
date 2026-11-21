"""Page grants shared by API admission and authenticated account responses."""

from typing import Literal

from fastapi import HTTPException
from libtv_agent.domain.enums import UserRole
from libtv_agent.domain.models import User

PagePermission = Literal["images", "make", "records", "library"]
PAGE_PERMISSIONS: tuple[PagePermission, ...] = ("images", "make", "records", "library")
MANAGEMENT_ADMIN_USERNAMES = frozenset({"classic-admin", "classic-admin-2"})


def management_admin(actor: User) -> bool:
    return actor.role == UserRole.ADMIN and actor.username in MANAGEMENT_ADMIN_USERNAMES


def page_permissions(actor: User) -> list[PagePermission]:
    if management_admin(actor):
        return list(PAGE_PERMISSIONS)
    return [
        page for page in PAGE_PERMISSIONS
        if page != "library"
        and (actor.page_permissions_json is None or page in actor.page_permissions_json)
    ]


def require_any_page(actor: User, *pages: PagePermission) -> None:
    if not set(page_permissions(actor)).intersection(pages):
        raise HTTPException(
            403,
            detail={"code": "PAGE_ACCESS_DENIED", "message": "账号未获此页面权限，请联系管理员。"},
        )
