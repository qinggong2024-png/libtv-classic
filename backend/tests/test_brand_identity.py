"""Confirmed aliases remain attached to accounts, generation, and CUT packages."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_account_access_budgets as accounts
import test_cutflow_handoff as shared
import test_cutflow_lan as lan
from libtv_agent.domain.models import Asset, Brand, DownloadArtifact, ParentTask, User
from libtv_agent.services.brand_identity import (
    append_brand_reference,
    brand_identity,
    brand_response,
)
from libtv_agent.services.cutflow_handoff import publish_package
from libtv_agent.services.cutflow_multi_adapter import build_multi_manifest
from libtv_agent.services.cutflow_site import CutflowSiteError, creation_snapshot, site_choices
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from sqlalchemy import select

base_handoff_env = shared.handoff_env
product_handoff_env = lan.product_handoff_env
handoff_env = lan.handoff_env
account_app = accounts.account_app


@pytest.mark.parametrize(("name", "profile", "label"), [
    ("y2k", "cherrykitten", "y2k/CherryKitten"),
    ("CherryKitten", "cherrykitten", "y2k/CherryKitten"),
    ("NW", "nerdywave", "NW / NerdyWave"),
    ("暗黑", "nerdywave", "暗黑 / NW / NerdyWave"),
    ("PW", "pagewings", "PW / PageWings"),
    ("IP", "ironpandafit", "IP / IronPandaFit"),
    ("TT", "tokyotiger", "TT / Tokyo-Tiger"),
    ("GC", "guidingcross", "GC / GuidingCross"),
    ("GuidingCross", "guidingcross", "GC / GuidingCross"),
    ("CLO", "classyoutlaw", "CLO / ClassyOutlaw"),
    ("ClassyOutlaw", "classyoutlaw", "CLO / ClassyOutlaw"),
    ("TRT", "theretrotee", "TRT / TheRetroTee"),
    ("TheRetroTee", "theretrotee", "TRT / TheRetroTee"),
    ("OR", "orionride", "OR / OrionRide"),
    ("OrionRide", "orionride", "OR / OrionRide"),
    ("BO", "boldoversize", "BO / BoldOversize"),
    ("BoldOversize", "boldoversize", "BO / BoldOversize"),
    ("NS", "goodnurselife", "NS / GoodNurseLife"),
])
async def test_existing_aliases_resolve_without_changing_account_brand(
    base_handoff_env, name, profile, label,
):
    database, _, _, ids = base_handoff_env
    async with database.session_factory() as session:
        brand = Brand(name=name)
        session.add(brand)
        await session.flush()
        user = await session.get(User, ids["user"])
        user.brand_id = brand.id
        await session.commit()
        choices, default = await site_choices(session, user.id)
        assert [item.id for item in choices] == [profile]
        assert default == profile
        assert brand_response(brand)["displayName"] == label
        frozen = await creation_snapshot(session, user.id, None)
        assert frozen["brandReference"]["accountBrandId"] == str(brand.id)
        assert frozen["brandReference"]["shortName"] == brand_identity(name).short_name
        assert frozen["profileId"] == profile
        with pytest.raises(CutflowSiteError):
            await creation_snapshot(session, user.id, "geeksoutfit")
        assert user.brand_id == brand.id and brand.name == name


async def test_brand_dropdown_api_enriches_existing_names_and_account_creation(account_app):
    app, client, ids = account_app
    headers = accounts.actor(ids, "root")
    for short, full, profile in (
        ("y2k", "CherryKitten", "cherrykitten"),
        ("NW", "NerdyWave", "nerdywave"), ("PW", "PageWings", "pagewings"),
        ("IP", "IronPandaFit", "ironpandafit"), ("TT", "Tokyo-Tiger", "tokyotiger"),
        ("GC", "GuidingCross", "guidingcross"), ("CLO", "ClassyOutlaw", "classyoutlaw"),
        ("TRT", "TheRetroTee", "theretrotee"), ("OR", "OrionRide", "orionride"),
        ("BO", "BoldOversize", "boldoversize"),
        ("NS", "GoodNurseLife", "goodnurselife"),
    ):
        response = await client.post("/api/v1/admin/brands", headers=headers, json={"name": short})
        assert response.status_code == 201
        brand = response.json()
        assert brand["displayName"] == ("y2k/CherryKitten" if short == "y2k" else f"{short} / {full}")
        assert brand["name"] == short and brand["siteProfileId"] == profile
        created = await client.post("/api/v1/admin/users", headers=headers, json={
            "username": f"test-{short}", "password": "synthetic-test-password",
            "brandId": brand["id"], "role": "USER",
        })
        assert created.status_code == 201, created.text
        assert created.json()["brandId"] == brand["id"]
        choices = await client.get("/api/v1/cutflow-handoffs/sites", headers={
            "X-User-Id": created.json()["id"],
        })
        assert choices.status_code == 200
        assert choices.json()["defaultProfileId"] == profile
        assert [(item["id"], item["name"]) for item in choices.json()["sites"]] == [
            (profile, "y2k/CherryKitten" if short == "y2k" else f"{short} / {full}"),
        ]
    listed = await client.get("/api/v1/admin/brands", headers=headers)
    assert {b["shortName"] for b in listed.json() if "shortName" in b} == {
        "y2k", "NW", "PW", "IP", "TT", "GC", "CLO", "TRT", "OR", "BO", "NS",
    }
    assert (await client.get("/api/v1/admin/brands", headers=accounts.actor(ids, "member"))).status_code == 403


async def test_cherrykitten_isolated_budget_and_nerdywave_alias_report(account_app):
    app, client, ids = account_app
    headers = accounts.actor(ids, "root")
    created = {}
    for name in ("NW", "y2k"):
        response = await client.post("/api/v1/admin/brands", headers=headers, json={"name": name})
        assert response.status_code == 201, response.text
        created[name] = response.json()["id"]
    # New alias rows must not multiply the same business brand.
    assert (await client.post("/api/v1/admin/brands", headers=headers,
                              json={"name": "暗黑"})).status_code == 409
    assert (await client.post("/api/v1/admin/brands", headers=headers,
                              json={"name": "CherryKitten"})).status_code == 409

    async with app.state.database.session_factory() as session:
        legacy = Brand(name="暗黑")
        session.add(legacy)
        await session.flush()
        for user_key, assigned in (
            ("member", legacy.id),
            ("other", uuid.UUID(created["NW"])),
            ("manager", uuid.UUID(created["y2k"])),
        ):
            user = await session.get(User, ids[user_key])
            user.brand_id = assigned
            user.image_price_microusd = 100_000
            await accounts.reserve_usd(
                session, user_id=user.id, kind="IMAGE",
                operation_id=uuid.uuid4(), units=1,
            )
        await session.commit()
    for brand, expected in (("NW", 0.2), ("y2k", 0.1)):
        result = await client.get("/api/v1/account/usage", headers=headers,
                                  params={"brandId": created[brand]})
        assert result.status_code == 200, result.text
        assert result.json()["reservedUsd"] == expected
        assert result.json()["total"] == (2 if brand == "NW" else 1)


@pytest.mark.parametrize(("profile_id", "short", "full"), [
    ("ironpandafit", "IP", "IronPandaFit"),
    ("cherrykitten", "y2k", "CherryKitten"),
])
async def test_lan_manifest_keeps_frozen_brand_reference(
    handoff_env, profile_id, short, full,
):
    database, settings, store, ids = handoff_env
    async with database.session_factory() as session:
        snapshot = await creation_snapshot(session, ids["user"], profile_id)
        task = await session.get(ParentTask, ids["task"])
        task.site_profile_id = snapshot["profileId"]
        task.site_profile_version = snapshot["profileVersion"]
        task.settings_json = {**(task.settings_json or {}), "cutflowSite": snapshot}
        await session.commit()
    row = await shared.enqueue(handoff_env)
    assert row.manifest_json["source"]["brand_reference"] == snapshot["brandReference"]
    async with database.session_factory() as session:
        artifact = await session.get(DownloadArtifact, ids["artifact"])
        references = list(await session.scalars(select(Asset).where(
            Asset.parent_task_id == ids["task"], Asset.role == "PRODUCT",
        )))
        await publish_package(row, artifact, settings, store,
                              product_references=references)
    manifest = json.loads((settings.cutflow_handoff_root / "inbox" / row.handoff_key
                           / "manifest.json").read_text())
    assert manifest["source"]["brand_reference"]["shortName"] == short
    assert manifest["source"]["brand_reference"]["fullName"] == full


def test_unknown_brands_are_not_guessed_or_conflated():
    assert brand_identity("cherrykitten").profile_id == "cherrykitten"
    assert brand_identity("Y2K").profile_id == "cherrykitten"
    assert SITE_STYLE_PROFILES["cherrykitten"].site_url == "https://www.cherrykitten.com/"
    assert brand_identity("SF") is None
    assert brand_identity("CS") is None
    assert brand_identity("TT").profile_id != "teachersgram"
    assert brand_identity("暗黑").profile_id != "magicstation"
    assert append_brand_reference("original prompt", None) == "original prompt"


def test_cut_multi_manifest_uses_cherrykitten_wire_brand_identity():
    owner, business, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    task = SimpleNamespace(
        id=task_id, user_id=owner, created_at=datetime.now(UTC),
        manifest_json={}, sources_json=[{
            "clipId": "clip-1", "order": 1,
            "relativePath": "assets/clip-1.mp4",
            "sha256": "a" * 64, "sizeBytes": 1024, "durationSeconds": 10,
            "userId": str(owner), "taskId": str(uuid.uuid4()),
            "variantId": str(uuid.uuid4()), "artifactId": str(uuid.uuid4()),
            "attemptId": str(uuid.uuid4()), "brandIdentity": str(business),
            "brandReference": {
                "profileId": "cherrykitten", "fullName": "CherryKitten",
                "shortName": "y2k", "accountBrandId": str(business),
            },
            "productReferences": [],
        }],
    )
    manifest = build_multi_manifest(task)
    assert manifest["sourceBinding"]["value"] == "cherrykitten"
    assert manifest["sourceBinding"]["businessBrandId"] == str(business)
    assert manifest["sources"][0]["brandIdentity"] == "cherrykitten"


async def test_nurse_abbreviation_does_not_activate_draft_style(base_handoff_env, monkeypatch):
    monkeypatch.setitem(SITE_STYLE_PROFILES, "goodnurselife", replace(
        SITE_STYLE_PROFILES["goodnurselife"], status="draft",
    ))
    database, _, _, ids = base_handoff_env
    async with database.session_factory() as session:
        brand = Brand(name="NS")
        session.add(brand)
        await session.flush()
        user = await session.get(User, ids["user"])
        user.brand_id = brand.id
        await session.commit()
        assert brand_response(brand)["displayName"] == "NS / GoodNurseLife"
        choices, default = await site_choices(session, user.id)
        assert choices == [] and default is None
        with pytest.raises(CutflowSiteError):
            await creation_snapshot(session, user.id, "goodnurselife")
