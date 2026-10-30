"""The Market page's data (docs/SPEC.md, F8 and F9): every tracked item with its current
price and changes, and price indices of item segments (game and rarity).

Only items whose prices are collected count: the portfolio's and the watchlist's. A crawl of
the whole market is out of reach at Steam's request rate (F7).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from invinv.analysis import CHANGE_WINDOWS, change_since, normalized_line
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.db.tables import Item
from invinv.portfolio import PERIODS, cached_item_prices
from invinv.pricing import PriceSource, listing_of, quote
from invinv.scheduling import RARITY_PRIORITY

# Segments with fewer items say more about single items than about the segment.
MIN_INDEX_ITEMS = 3
# Steam's item kinds without a rarity word (trading cards and other Steam items).
_KINDS = ("Foil Trading Card", "Trading Card", "Profile Background", "Emoticon")


@dataclass(frozen=True)
class MarketRow:
    item: Item
    segment: str | None  # rarity (or kind) of the item, e.g. "Immortal", "Covert"
    price: float | None
    price_source: PriceSource | None
    changes: dict[str, float | None]  # per key of CHANGE_WINDOWS and of PERIODS
    sales_per_day: float | None  # last 30 days
    robust_daily: pd.Series | None


@dataclass(frozen=True)
class IndexLine:
    appid: int
    segment: str
    items: int
    line: pd.Series  # daily, 100 at the start of the period
    change: float | None


def segment_of(item: Item) -> str | None:
    if item.rarity:
        return item.rarity
    item_type = item.item_type or ""
    for word in item_type.split():
        if word in RARITY_PRIORITY and word != "★":
            return word
    return next((kind for kind in _KINDS if kind in item_type), None)


async def load_rows(sessions: SessionMaker, now: datetime) -> list[MarketRow]:
    async with sessions() as session:
        items = {item.id: item for _, item in await repo.active_holdings(session)}
        items |= {item.id: item for item in await repo.watchlist_items(session)}
    prices = await cached_item_prices(sessions, list(items.values()))
    rows = []
    for item in items.values():
        computed = prices[item.id]
        current = quote(listing_of(item), computed.sales_price, now)
        price = current.price if current else None
        robust = computed.robust
        rows.append(
            MarketRow(
                item=item,
                segment=segment_of(item),
                price=price,
                price_source=current.source if current else None,
                changes={
                    key: change_since(robust, price, span, now)
                    if robust is not None and price is not None
                    else None
                    for key, span in (CHANGE_WINDOWS | PERIODS).items()
                },
                sales_per_day=computed.metrics.avg_daily_volume_30d if computed.metrics else None,
                robust_daily=computed.daily,
            )
        )
    return rows


def indices(rows: Sequence[MarketRow], period: timedelta | None, now: datetime) -> list[IndexLine]:
    """The median of the segment's items, each at 100 at the start of the period, so a few
    items that jumped many times over do not carry the segment; segments with at least
    MIN_INDEX_ITEMS priced items, largest first."""
    groups: dict[tuple[int, str], list[pd.Series]] = {}
    for row in rows:
        if row.segment is None or row.robust_daily is None or row.robust_daily.empty:
            continue
        line = normalized_line(row.robust_daily, period, now)
        if not line.empty:
            groups.setdefault((row.item.appid, row.segment), []).append(line)
    result = []
    for (appid, segment), lines in groups.items():
        if len(lines) < MIN_INDEX_ITEMS:
            continue
        start = min(line.index[0] for line in lines)
        days = pd.date_range(start.normalize(), pd.Timestamp(now).normalize(), freq="D")
        aligned = [
            line.groupby(pd.DatetimeIndex(line.index).normalize()).last().reindex(days).ffill()
            for line in lines
        ]
        index = pd.concat(aligned, axis=1).median(axis=1, skipna=True).dropna()
        change = float(index.iloc[-1] / 100 - 1) if not index.empty else None
        result.append(IndexLine(appid, segment, len(lines), index, change))
    result.sort(key=lambda i: -i.items)
    return result
