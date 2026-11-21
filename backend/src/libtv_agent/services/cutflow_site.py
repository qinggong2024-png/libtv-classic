"""Freeze confirmed storefront identity on the source task, never guess at send time."""

import ipaddress
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from libtv_agent.domain.models import AuditEvent, Brand, CutflowHandoff, ParentTask, User
from libtv_agent.services.brand_identity import matches_brand, profile_brand_reference
from libtv_agent.services.content_catalog import list_merged_site_style_profiles
from sqlalchemy import select, update

SNAPSHOT_KEY = "cutflowSite"


class CutflowSiteError(ValueError):
    pass


async def site_choices(session, user_id: uuid.UUID):
    actor = await session.get(User, user_id)
    if actor is None or not actor.is_active:
        raise CutflowSiteError("账号不可用")
    session.info.update(actor_id=actor.id, brand_id=actor.brand_id)
    profiles = [profile for profile in await list_merged_site_style_profiles(session)
                if profile.status == "active" and profile.library_scope == "brand"
                and profile.evidence_level == "verified_site"]
    default = None
    if actor.brand_id:
        brand = await session.get(Brand, actor.brand_id)
        # Only the existing, explicitly assigned brand can supply an account default.
        profiles = [profile for profile in profiles if brand and matches_brand(brand.name, profile)]
        if len(profiles) == 1:
            default = profiles[0].id
    return profiles, default


async def creation_snapshot(session, user_id: uuid.UUID, profile_id: str | None):
    profiles, default = await site_choices(session, user_id)
    selected = profile_id or default
    if not selected:
        return None
    profile = next((item for item in profiles if item.id == selected), None)
    if profile is None:
        raise CutflowSiteError("该商品站点未启用或不属于当前账号品牌")
    address = urlsplit(profile.site_url)
    host = address.hostname
    if (address.scheme != "https" or not host or "." not in host
            or address.netloc.lower() != host or address.path not in {"", "/"}
            or address.query or address.fragment
            or ".." in host
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)):
        raise CutflowSiteError("商品站点没有有效的正式域名")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CutflowSiteError("不能将服务器 IP 作为商品站点")
    url = f"https://{host}/"
    actor = await session.get(User, user_id)
    brand = await session.get(Brand, actor.brand_id) if actor.brand_id else None
    return {
        "profileId": profile.id, "profileVersion": profile.version,
        "name": profile.name, "siteUrl": url,
        "siteId": str(uuid.uuid5(uuid.NAMESPACE_URL, url)),
        "userId": str(user_id),
        "selection": "explicit" if profile_id else "account_brand",
        "recordedAt": datetime.now(UTC).isoformat(),
        "brandReference": profile_brand_reference(profile, brand),
    }


async def lock_source_task(session, user_id, task_id):
    # Both binding and enqueue take this write lock, including on SQLite.
    await session.execute(update(ParentTask).where(
        ParentTask.id == task_id, ParentTask.user_id == user_id,
    ).values(title=ParentTask.title, updated_at=ParentTask.updated_at)
        .execution_options(synchronize_session=False))


async def bind_missing_task_site(session, user_id, task_id, profile_id):
    await lock_source_task(session, user_id, task_id)
    task = await session.scalar(select(ParentTask).where(
        ParentTask.id == task_id, ParentTask.user_id == user_id,
    ).execution_options(populate_existing=True))
    if task is None:
        raise CutflowSiteError("原任务不存在或不属于当前账号")
    existing = (task.settings_json or {}).get(SNAPSHOT_KEY)
    if isinstance(existing, dict) and existing.get("profileId") == profile_id:
        return existing
    if existing or task.site_profile_id:
        raise CutflowSiteError("原任务已有站点关联，不能通过补充入口更换")
    if await session.scalar(select(CutflowHandoff.id).where(
        CutflowHandoff.parent_task_id == task.id,
    ).limit(1)):
        raise CutflowSiteError("已有交接的任务不能更改来源站点")
    snapshot = await creation_snapshot(session, user_id, profile_id)
    if snapshot is None:
        raise CutflowSiteError("请选择已确认的商品站点")
    task.site_profile_id = snapshot["profileId"]
    task.site_profile_version = snapshot["profileVersion"]
    task.settings_json = {**(task.settings_json or {}), SNAPSHOT_KEY: snapshot}
    session.add(AuditEvent(
        actor_id=user_id, action="BIND_MISSING_CUTFLOW_SITE", entity_type="parent_task",
        entity_id=str(task.id), reason="owner confirmed source storefront",
        correlation_id=str(uuid.uuid4()), evidence_refs_json=[snapshot],
    ))
    await session.flush()
    return snapshot
