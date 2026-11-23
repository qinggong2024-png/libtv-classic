"""Confirmed brand aliases; identity metadata is separate from creative style rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandIdentity:
    profile_id: str
    full_name: str
    short_name: str
    aliases: tuple[str, ...]

    @property
    def label(self) -> str:
        if self.profile_id == "cherrykitten":
            return f"{self.short_name}/{self.full_name}"
        return f"{self.short_name} / {self.full_name}"


# NW/PW/GK/TSG/PPH: user-supplied v1.13.8 Actual_Brand_Cards.md.
# IP/TT and the remaining abbreviations: user confirmations/screenshots, 2026-10-21.
BRAND_IDENTITIES = (
    BrandIdentity("cherrykitten", "CherryKitten", "y2k", (
        "Y2K / CherryKitten", "y2k/CherryKitten", "CK",
    )),
    BrandIdentity("nerdywave", "NerdyWave", "NW", ("暗黑", "暗黑品牌")),
    BrandIdentity("pagewings", "PageWings", "PW", ("泛读书",)),
    BrandIdentity("ironpandafit", "IronPandaFit", "IP", ("健身",)),
    BrandIdentity("tokyotiger", "Tokyo-Tiger", "TT", ("东京",)),
    BrandIdentity("geeksoutfit", "GeeksOutfit", "GK", ("极客",)),
    BrandIdentity("teachersgram", "Teachersgram", "TSG", ("Teacher", "教师", "教师站")),
    BrandIdentity("magicstation", "PurpleHecate", "PPH", ("Purple Hecate", "魔法")),
    BrandIdentity("guidingcross", "GuidingCross", "GC", ("基督",)),
    BrandIdentity("classyoutlaw", "ClassyOutlaw", "CLO", ("怪诞",)),
    BrandIdentity("theretrotee", "TheRetroTee", "TRT", ("怀旧",)),
    BrandIdentity("orionride", "OrionRide", "OR", ("骑行", "骑行服")),
    BrandIdentity("boldoversize", "BoldOversize", "BO", ("大码",)),
    BrandIdentity("goodnurselife", "GoodNurseLife", "NS", ("护士",)),
)


def brand_identity(name: str) -> BrandIdentity | None:
    value = name.strip().casefold()
    for identity in BRAND_IDENTITIES:
        aliases = (identity.profile_id, identity.full_name, identity.short_name, *identity.aliases)
        if value in {alias.casefold() for alias in (
            *aliases, identity.label, f"{identity.full_name} / {identity.short_name}",
        )}:
            return identity
    return None


def brand_response(brand) -> dict:
    identity = brand_identity(brand.name)
    result = {"id": str(brand.id), "name": brand.name, "displayName": brand.name}
    if identity:
        label = identity.label
        if brand.name.casefold() not in {
            identity.short_name.casefold(), identity.full_name.casefold(), label.casefold(),
        }:
            label = f"{brand.name} / {label}"
        result.update(displayName=label, shortName=identity.short_name,
                      fullName=identity.full_name, siteProfileId=identity.profile_id)
    return result


def matches_brand(name: str, profile) -> bool:
    identity = brand_identity(name)
    if identity:
        return profile.id == identity.profile_id
    return name.strip().casefold() in {
        part.strip().casefold() for part in (profile.id, profile.name, *profile.name.split("/"))
    }


def profile_brand_reference(profile, brand=None) -> dict:
    identity = brand_identity(profile.id)
    return {
        "profileId": profile.id,
        "fullName": identity.full_name if identity else profile.name,
        "shortName": identity.short_name if identity else None,
        "displayName": identity.label if identity else profile.name,
        "siteUrl": profile.site_url,
        **({"accountBrandId": str(brand.id), "accountBrandName": brand.name} if brand else {}),
    }


def append_brand_reference(prompt: str, reference: dict | None) -> str:
    if not reference:
        return prompt
    label = reference["displayName"]
    return (
        f"{prompt} Brand reference: {label}. "
        "Keep the current product references authoritative; do not add or replace "
        "logos, lettering, or artwork based on the brand name."
    )
