"""Typed access to the Steam Community Market endpoints we use (see docs/SPEC.md, section 2)."""

import json
from datetime import UTC, datetime

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
        self, query: str, *, priority: bool = False, max_wait: float | None = None
    ) -> list[SearchResult]:
        """Search the catalog. Steam returns at most 10 results per request."""
        payload = await self._client.get_json(
            "/market/search/render/",
            params={"appid": str(self._appid), "norender": "1", "query": query},
            priority=priority,
            max_wait=max_wait,
        )
        return parse_search_results(payload)
