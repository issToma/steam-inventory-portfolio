"""Item search shared by the UI and the API: the local catalog first, Steam on request."""

import asyncio
import time
from dataclasses import dataclass, replace

from cs2market.core.parsers import SearchResult
from cs2market.core.steam import CATEGORY_BY_KIND, EXTERIOR_NAMES, Exterior, ItemKind, Quality
from cs2market.db import repo
from cs2market.runtime import runtime

# A Steam search waits for the shared client: at most one background request plus the
# request interval. It gives up instead of waiting out a 429 cool-down.
STEAM_MAX_WAIT = 60.0
STEAM_TIMEOUT = 150.0
STEAM_CACHE_TTL = 600.0


@dataclass(frozen=True)
class SearchQuery:
    text: str
    kind: ItemKind | None = None
    quality: Quality | None = None
    exterior: Exterior | None = None


_steam_cache: dict[SearchQuery, tuple[float, list[SearchResult]]] = {}


async def search_local(query: SearchQuery, limit: int = 20) -> list[SearchResult]:
    """Instant, typo-tolerant search in the items we have already seen. No Steam requests."""
    async with runtime.sessions() as session:
        entries = await repo.search_catalog(
            session,
            query.text,
            category=CATEGORY_BY_KIND[query.kind] if query.kind else None,
            quality=query.quality,
            exterior_name=EXTERIOR_NAMES[query.exterior] if query.exterior else None,
            limit=limit,
        )
    return [
        SearchResult(
            market_hash_name=e.market_hash_name,
            item_type=e.item_type or "",
            bucket_group_id=e.bucket_group_id,
            classid=e.classid,
            icon_url=e.icon_url,
            sell_listings=e.sell_listings,
            sell_price_usd_cents=e.sell_price_usd_cents,
        )
        for e in entries
    ]


async def search_steam(query: SearchQuery) -> list[SearchResult]:
    """Search Steam ahead of background requests and remember the results in the catalog.

    Raises SteamBusyError during a 429 cool-down and TimeoutError if the wait is too long.
    """
    key = replace(query, text=query.text.casefold())
    cached = _steam_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < STEAM_CACHE_TTL:
        return cached[1]
    results = await asyncio.wait_for(
        runtime.market.search(
            query.text,
            kind=query.kind,
            quality=query.quality,
            exterior=query.exterior,
            priority=True,
            max_wait=STEAM_MAX_WAIT,
        ),
        timeout=STEAM_TIMEOUT,
    )
    _steam_cache[key] = (time.monotonic(), results)
    async with runtime.sessions() as session:
        await repo.upsert_catalog(
            session,
            [
                repo.CatalogRecord(
                    market_hash_name=r.market_hash_name,
                    item_type=r.item_type,
                    bucket_group_id=r.bucket_group_id,
                    classid=r.classid,
                    icon_url=r.icon_url,
                    sell_price_usd_cents=r.sell_price_usd_cents,
                    sell_listings=r.sell_listings,
                )
                for r in results
            ],
        )
        await session.commit()
    return results
