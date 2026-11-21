"""Confirmed aliases remain attached to accounts, generation, and CUT packages."""

import json
from dataclasses import replace

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
from libtv_agent.services.cutflow_site import CutflowSiteError, creation_snapshot, site_choices
from libtv_agent.templates.site_style_profiles import SITE_STYLE_PROFILES
from sqlalchemy import select

base_handoff_env = shared.handoff_env
product_handoff_env = lan.product_handoff_env
handoff_env = lan.handoff_env
account_app = accounts.account_app


@pytest.mark.parametrize(("name", "profile", "label"), [
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
        assert brand["displayName"] == f"{short} / {full}"
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
            (profile, f"{short} / {full}"),
        ]
    listed = await client.get("/api/v1/admin/brands", headers=headers)
    assert {b["shortName"] for b in listed.json() if "shortName" in b} == {
        "NW", "PW", "IP", "TT", "GC", "CLO", "TRT", "OR", "BO", "NS",
    }
    assert (await client.get("/api/v1/admin/brands", headers=accounts.actor(ids, "member"))).status_code == 403


async def test_lan_manifest_keeps_frozen_brand_reference(handoff_env):
    database, settings, store, ids = handoff_env
    async with database.session_factory() as session:
        snapshot = await creation_snapshot(session, ids["user"], "ironpandafit")
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
    assert manifest["source"]["brand_reference"]["shortName"] == "IP"
    assert manifest["source"]["brand_reference"]["fullName"] == "IronPandaFit"


def test_unknown_brands_are_not_guessed_or_conflated():
    assert brand_identity("SF") is None
    assert brand_identity("CS") is None
    assert brand_identity("TT").profile_id != "teachersgram"
    assert brand_identity("暗黑").profile_id != "magicstation"
    assert append_brand_reference("original prompt", None) == "original prompt"


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
