"""Typed access to the Steam Community Market endpoints we use (see docs/SPEC.md, section 2)."""

import json
from datetime import UTC, datetime
from typing import Literal

from cs2market.core.http import SteamClient
from cs2market.core.parsers import (
    PriceHistory,
    PriceOverview,
    SearchResult,
    parse_price_history,
    parse_price_overview,
    parse_search_results,
)

CS2_APPID = 730

# The market's React frontend sends this header with its query actions.
_QUERY_ACTION = {"x-valve-request-type": "queryAction"}

type Quality = Literal["normal", "stattrak", "souvenir"]

# Steam's quality tags. Knives and gloves (★) carry the "unusual" tags, so "normal" and
# "stattrak" include them too.
QUALITY_TAGS: dict[Quality, tuple[str, ...]] = {
    "normal": ("tag_normal", "tag_unusual"),
    "stattrak": ("tag_strange", "tag_unusual_strange"),
    "souvenir": ("tag_tournament",),
}

type ItemKind = Literal[
    "rifle",
    "sniper_rifle",
    "pistol",
    "smg",
    "shotgun",
    "machinegun",
    "knife",
    "gloves",
    "sticker",
    "case",
    "agent",
    "graffiti",
    "patch",
    "charm",
    "music_kit",
    "key",
]

# Steam's item type tags (category_730_Type[]); checked against /market/appfilters/730.
TYPE_TAGS: dict[ItemKind, str] = {
    "rifle": "tag_CSGO_Type_Rifle",
    "sniper_rifle": "tag_CSGO_Type_SniperRifle",
    "pistol": "tag_CSGO_Type_Pistol",
    "smg": "tag_CSGO_Type_SMG",
    "shotgun": "tag_CSGO_Type_Shotgun",
    "machinegun": "tag_CSGO_Type_Machinegun",
    "knife": "tag_CSGO_Type_Knife",
    "gloves": "tag_Type_Hands",
    "sticker": "tag_CSGO_Tool_Sticker",
    "case": "tag_CSGO_Type_WeaponCase",
    "agent": "tag_Type_CustomPlayer",
    "graffiti": "tag_CSGO_Type_Spray",
    "patch": "tag_CSGO_Tool_Patch",
    "charm": "tag_CSGO_Tool_Keychain",
    "music_kit": "tag_CSGO_Type_MusicKit",
    "key": "tag_CSGO_Tool_WeaponCase_KeyTag",
}

# Only these kinds have wear and StatTrak/Souvenir variants.
SKIN_KINDS: frozenset[ItemKind] = frozenset(
    {"rifle", "sniper_rifle", "pistol", "smg", "shotgun", "machinegun", "knife", "gloves"}
)

type Exterior = Literal["fn", "mw", "ft", "ww", "bs"]

EXTERIOR_TAGS: dict[Exterior, str] = {
    "fn": "tag_WearCategory0",
    "mw": "tag_WearCategory1",
    "ft": "tag_WearCategory2",
    "ww": "tag_WearCategory3",
    "bs": "tag_WearCategory4",
}


class SteamMarket:
    def __init__(self, client: SteamClient, appid: int = CS2_APPID) -> None:
        self._client = client
        self._appid = appid

    async def price_history(self, market_hash_name: str) -> PriceHistory:
        payload = await self._client.get_json(
            "/market/actions",
            params={
                "q": "QueryPriceHistory",
                "qp": json.dumps([self._appid, market_hash_name], ensure_ascii=False),
            },
            headers=_QUERY_ACTION,
        )
        return parse_price_history(payload, fetched_at=datetime.now(UTC))

    async def price_overview(self, market_hash_name: str, currency: int) -> PriceOverview:
        payload = await self._client.get_json(
            "/market/priceoverview/",
            params={
                "appid": str(self._appid),
                "currency": str(currency),
                "market_hash_name": market_hash_name,
            },
        )
        return parse_price_overview(payload)

    async def search(
        self,
        query: str,
        *,
        kind: ItemKind | None = None,
        quality: Quality | None = None,
        exterior: Exterior | None = None,
        priority: bool = False,
        max_wait: float | None = None,
    ) -> list[SearchResult]:
        """Search the catalog with optional Steam-side filters. At most 10 results.

        An exterior filter excludes everything without wear (stickers, cases, ...), so the UI
        only offers it together with a skin kind.
        """
        category = f"category_{self._appid}_"
        params = [("appid", str(self._appid)), ("norender", "1"), ("query", query)]
        if kind is not None:
            params.append((f"{category}Type[]", TYPE_TAGS[kind]))
        if quality is not None:
            params += [(f"{category}Quality[]", tag) for tag in QUALITY_TAGS[quality]]
        if exterior is not None:
            params.append((f"{category}Exterior[]", EXTERIOR_TAGS[exterior]))
        payload = await self._client.get_json(
            "/market/search/render/", params=params, priority=priority, max_wait=max_wait
        )
        return parse_search_results(payload)
