"""Pure functions that turn raw Steam responses into models.

Every parser raises SteamFormatError when the response does not look like what we saw
during the 2026-10-26 probe, so a silent Steam change fails loudly instead of storing junk.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from cs2market.core.currency import parse_price_text

Resolution = Literal["day", "hour"]

# Steam returns hourly points for roughly the last 30 days and daily points before that.
HOURLY_WINDOW = timedelta(days=30)


class SteamFormatError(Exception):
    """A Steam response did not have the expected shape."""


@dataclass(frozen=True)
class HistoryPoint:
    ts: datetime
    resolution: Resolution
    price: Decimal
    volume: int


@dataclass(frozen=True)
class PriceHistory:
    currency: int
    points: list[HistoryPoint]


@dataclass(frozen=True)
class PriceOverview:
    lowest_price: Decimal | None
    median_price: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class SearchResult:
    market_hash_name: str
    item_type: str
    bucket_group_id: str | None
    classid: str | None
    icon_url: str | None
    # Unknown for entries from the local catalog that were never in a Steam search result.
    sell_listings: int | None
    sell_price_usd_cents: int | None


@dataclass(frozen=True)
class ItemTraits:
    rarity: str | None
    category: str | None
    quality: str | None
    exterior: str | None


def _require(payload: Any, key: str, kind: type | tuple[type, ...], source: str) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), kind):
        raise SteamFormatError(f"{source}: missing or malformed field {key!r}")
    return payload[key]


def parse_price_history(payload: Any, fetched_at: datetime) -> PriceHistory:
    """Parse /market/actions?q=QueryPriceHistory.

    A point is hourly when it is inside Steam's hourly window or not aligned to midnight UTC.
    """
    source = "QueryPriceHistory"
    return _parse_history_data(_require(payload, "data", dict, source), fetched_at, source)


def _parse_history_data(data: Any, fetched_at: datetime, source: str) -> PriceHistory:
    currency = _require(data, "ecurrency", int, source)
    raw_points = _require(data, "prices", list, source)

    hourly_from = (fetched_at - HOURLY_WINDOW).replace(hour=0, minute=0, second=0, microsecond=0)
    points = []
    for raw in raw_points:
        time = _require(raw, "time", int, source)
        price = _require(raw, "price_median", (int, float), source)
        volume = _require(raw, "purchases", int, source)
        ts = datetime.fromtimestamp(time, UTC)
        hourly = ts >= hourly_from or time % 86400 != 0
        points.append(
            HistoryPoint(
                ts=ts,
                resolution="hour" if hourly else "day",
                price=Decimal(repr(float(price))).quantize(Decimal("0.0001")),
                volume=volume,
            )
        )
    return PriceHistory(currency=currency, points=points)


@dataclass(frozen=True)
class GroupVariant:
    """One variant (wear, StatTrak, Souvenir) found on a market group page."""

    market_hash_name: str
    item_type: str | None
    classid: str | None
    icon_url: str | None
    collection: str | None
    history: PriceHistory | None


_SSR_DATA = re.compile(r'<script[^>]*\bid="valve-ssr-data"[^>]*>(.*?)</script>', re.S)


def parse_group_page(html: str, fetched_at: datetime) -> dict[str, GroupVariant]:
    """Parse /market/listings/730/<bucket_group_id>.

    The page is rendered on the server and embeds its react-query cache as JSON. Among
    other things it holds the price history and description of every variant in the group.
    """
    source = "group page"
    match = _SSR_DATA.search(html)
    if match is None:
        raise SteamFormatError(f"{source}: no valve-ssr-data block")
    try:
        ssr = json.loads(match.group(1))
        query_data = json.loads(
            _require(_require(ssr, "renderContext", dict, source), "queryData", str, source)
        )
    except ValueError as exc:
        raise SteamFormatError(f"{source}: embedded data is not JSON") from exc

    histories: dict[str, PriceHistory] = {}
    descriptions: dict[str, dict[str, Any]] = {}
    for query in _require(query_data, "queries", list, source):
        key = query.get("queryKey") if isinstance(query, dict) else None
        if not (isinstance(key, list) and len(key) == 4 and key[0] == "market"):
            continue
        data = (query.get("state") or {}).get("data")
        name = key[3]
        if key[1] == "pricehistory" and isinstance(data, dict):
            histories[name] = _parse_history_data(data, fetched_at, source)
        elif key[1] == "description" and isinstance(data, dict):
            descriptions[name] = data
    if not histories:
        raise SteamFormatError(f"{source}: no price history in the embedded data")

    variants = {}
    for name in histories.keys() | descriptions.keys():
        description = descriptions.get(name, {})
        collection = next(
            (
                d.get("value")
                for d in description.get("descriptions", [])
                if isinstance(d, dict) and d.get("name") == "itemset_name"
            ),
            None,
        )
        variants[name] = GroupVariant(
            market_hash_name=name,
            item_type=description.get("type"),
            classid=description.get("classid"),
            icon_url=description.get("icon_url"),
            collection=collection,
            history=histories.get(name),
        )
    return variants


def parse_price_overview(payload: Any) -> PriceOverview:
    """Parse /market/priceoverview. Fields are missing when the item has no listings or sales."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SteamFormatError("priceoverview: success is not true")

    def price(key: str) -> Decimal | None:
        value = payload.get(key)
        return parse_price_text(value) if isinstance(value, str) else None

    volume = payload.get("volume")
    return PriceOverview(
        lowest_price=price("lowest_price"),
        median_price=price("median_price"),
        volume=int(volume.replace(",", "")) if isinstance(volume, str) else None,
    )


def parse_search_results(payload: Any) -> list[SearchResult]:
    """Parse /market/search/render?norender=1."""
    source = "search/render"
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SteamFormatError(f"{source}: success is not true")
    results = []
    for raw in _require(payload, "results", list, source):
        description = _require(raw, "asset_description", dict, source)
        results.append(
            SearchResult(
                market_hash_name=_require(raw, "hash_name", str, source),
                item_type=_require(description, "type", str, source),
                bucket_group_id=description.get("market_bucket_group_id"),
                classid=description.get("classid"),
                icon_url=description.get("icon_url"),
                sell_listings=_require(raw, "sell_listings", int, source),
                sell_price_usd_cents=_require(raw, "sell_price", int, source),
            )
        )
    return results


_RARITIES = (
    "Consumer Grade",
    "Industrial Grade",
    "Mil-Spec Grade",
    "Mil-Spec",
    "Restricted",
    "Classified",
    "Covert",
    "Contraband",
    "Base Grade",
    "High Grade",
    "Remarkable",
    "Exotic",
    "Extraordinary",
    "Distinguished",
    "Exceptional",
    "Superior",
    "Master",
)
_EXTERIOR = re.compile(r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$")


def parse_item_traits(market_hash_name: str, item_type: str) -> ItemTraits:
    """Split a type like "StatTrak™ Classified Rifle" into quality, rarity and category."""
    rest = item_type.strip()
    qualities = []
    for prefix in ("★", "StatTrak™", "Souvenir"):
        if rest.startswith(prefix):
            qualities.append(prefix)
            rest = rest[len(prefix) :].strip()

    rarity = next((r for r in _RARITIES if rest == r or rest.startswith(f"{r} ")), None)
    category = rest[len(rarity) :].strip() if rarity else rest
    exterior = _EXTERIOR.search(market_hash_name)
    return ItemTraits(
        rarity=rarity,
        category=category or None,
        quality=" ".join(qualities) or None,
        exterior=exterior.group(1) if exterior else None,
    )


def market_hash_name_from_input(text: str) -> str:
    """Accept a market_hash_name or a market listing URL and return the market_hash_name.

    Group URLs (/listings/730/G...) without a variant cannot be resolved to one item.
    """
    text = text.strip()
    if not text.startswith(("http://", "https://")):
        return text
    parts = [unquote(p) for p in urlparse(text).path.split("/") if p]
    try:
        tail = parts[parts.index("listings") + 2 :]
    except ValueError:
        raise ValueError("not a Steam market listing URL") from None
    if not tail:
        raise ValueError("the URL has no item name")
    name = tail[-1]
    if len(tail) == 1 and re.fullmatch(r"G[0-9A-F]+", name):
        raise ValueError("this URL points to an item group; open a specific variant instead")
    return name
