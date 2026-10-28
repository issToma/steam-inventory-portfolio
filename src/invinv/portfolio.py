"""Portfolio valuation: positions priced in USD, totals and the value over time."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import batched

import pandas as pd

from invinv.analysis import (
    Metrics,
    MissingRateError,
    change_over,
    compute_metrics,
    daily_series,
    price_views,
    recent_trend,
    to_frame,
)
from invinv.core.fees import PUBLISHER_FEE_RATE, seller_proceeds
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.db.tables import Item
from invinv.pricing import Listing, PriceSource, listing_of, quote, recent_sales_price

# Periods offered for the portfolio's statistics, movers and chart; None = all history.
PERIODS: dict[str, timedelta | None] = {
    "7D": timedelta(days=7),
    "30D": timedelta(days=30),
    "1Y": timedelta(days=365),
    "All": None,
}


@dataclass(frozen=True)
class PeriodStats:
    start: float
    end: float
    change: float
    change_pct: float | None
    high: float
    high_on: pd.Timestamp
    low: float
    low_on: pd.Timestamp


def period_stats(history: pd.Series, period: timedelta | None) -> PeriodStats | None:
    """How the portfolio value moved over the period, from the daily value history."""
    if period is not None and not history.empty:
        history = history[history.index >= history.index[-1] - period]
    history = history[history > 0]
    if history.empty:
        return None
    start, end = float(history.iloc[0]), float(history.iloc[-1])
    days = pd.DatetimeIndex(history.index)
    values = history.to_numpy()
    return PeriodStats(
        start=start,
        end=end,
        change=end - start,
        change_pct=end / start - 1 if start else None,
        high=float(history.max()),
        high_on=days[int(values.argmax())],
        low=float(history.min()),
        low_on=days[int(values.argmin())],
    )


@dataclass(frozen=True)
class PositionInput:
    holding_id: int
    item_id: int
    name: str
    source: str
    location: str | None
    quantity: int
    buy_price_usd: Decimal | None
    history: pd.DataFrame  # to_frame() output, prices in USD
    icon_url: str | None = None
    rarity: str | None = None
    quality: str | None = None
    pinned: bool = False
    appid: int = field(kw_only=True)
    name_color: str | None = None
    publisher_fee: float = PUBLISHER_FEE_RATE
    # Computed from `history` when not given; the app passes cached ones (ItemPrices).
    prices: ItemPrices | None = None
    listing: Listing | None = None


@dataclass(frozen=True)
class ItemPrices:
    """What the valuation needs from one item's price history, computed once per history."""

    metrics: Metrics | None
    daily: pd.Series | None  # daily price, for the portfolio's value over time
    trend: tuple[float, ...]
    changes: tuple[tuple[str, float | None], ...]  # per key of PERIODS
    sales_price: float | None  # pricing.recent_sales_price(), when nothing is listed


def item_prices(history: pd.DataFrame, publisher_fee: float = PUBLISHER_FEE_RATE) -> ItemPrices:
    views = price_views(history) if not history.empty else None
    prices = views.prices if views else pd.Series(dtype=float)
    return ItemPrices(
        sales_price=recent_sales_price(views) if views else None,
        metrics=compute_metrics(history, views, publisher_fee),
        daily=views.daily["price"] if views else None,
        trend=tuple(recent_trend(history, views=views)),
        changes=tuple((key, change_over(prices, span)) for key, span in PERIODS.items()),
    )


@dataclass(frozen=True)
class Position:
    holding_id: int
    item_id: int
    name: str
    source: str
    location: str | None
    quantity: int
    price: float | None
    value: float | None
    proceeds: float | None
    cost: float | None
    pnl: float | None
    share: float | None
    change_24h: float | None
    change_7d: float | None
    change_30d: float | None
    icon_url: str | None = None
    rarity: str | None = None
    quality: str | None = None
    trend: tuple[float, ...] = ()
    pinned: bool = False
    appid: int = field(kw_only=True)
    name_color: str | None = None
    # "listing" (the cheapest listing now) or "sales" (nothing listed: recent sales).
    price_source: PriceSource | None = None
    # Price change per period key of PERIODS.
    changes: tuple[tuple[str, float | None], ...] = ()

    def change(self, period: str) -> float | None:
        return dict(self.changes).get(period)


@dataclass(frozen=True)
class Portfolio:
    positions: list[Position]
    value: float
    proceeds: float
    # Totals over the positions that have a purchase price and a current price.
    cost: float
    pnl: float
    unpriced: int
    value_history: pd.Series  # daily, USD


def value_positions(inputs: Sequence[PositionInput], now: datetime | None = None) -> Portfolio:
    """Price every position at its item's current price (pricing.quote)."""
    now = now or datetime.now(UTC)
    rows = []
    dailies: dict[int, pd.Series] = {}
    for p in inputs:
        computed = p.prices or item_prices(p.history, p.publisher_fee)
        if computed.daily is not None:
            dailies[p.holding_id] = computed.daily
        metrics = computed.metrics
        current = quote(p.listing, computed.sales_price, now)
        price = current.price if current else None
        unit_proceeds = (
            seller_proceeds(round(price * 100), p.publisher_fee) / 100
            if price is not None
            else None
        )
        cost = float(p.buy_price_usd) * p.quantity if p.buy_price_usd is not None else None
        proceeds = unit_proceeds * p.quantity if unit_proceeds is not None else None
        rows.append(
            (
                p,
                price,
                price * p.quantity if price is not None else None,
                proceeds,
                cost,
                proceeds - cost if proceeds is not None and cost is not None else None,
                metrics,
                computed,
                current.source if current else None,
            )
        )
    total = sum(value for _, _, value, *_ in rows if value is not None)
    positions = [
        Position(
            holding_id=p.holding_id,
            item_id=p.item_id,
            name=p.name,
            source=p.source,
            location=p.location,
            quantity=p.quantity,
            price=price,
            value=value,
            proceeds=proceeds,
            cost=cost,
            pnl=pnl,
            share=value / total if value is not None and total else None,
            change_24h=metrics.change_24h if metrics else None,
            change_7d=metrics.change_7d if metrics else None,
            change_30d=metrics.change_30d if metrics else None,
            icon_url=p.icon_url,
            rarity=p.rarity,
            quality=p.quality,
            trend=computed.trend,
            pinned=p.pinned,
            appid=p.appid,
            name_color=p.name_color,
            changes=computed.changes,
            price_source=source,
        )
        for p, price, value, proceeds, cost, pnl, metrics, computed, source in rows
    ]
    positions.sort(key=lambda pos: pos.value or 0, reverse=True)
    with_pnl = [pos for pos in positions if pos.pnl is not None]
    return Portfolio(
        positions=positions,
        value=total,
        proceeds=sum(pos.proceeds for pos in positions if pos.proceeds is not None),
        cost=sum(pos.cost for pos in with_pnl if pos.cost is not None),
        pnl=sum(pos.pnl for pos in with_pnl if pos.pnl is not None),
        unpriced=sum(1 for pos in positions if pos.price is None),
        value_history=value_history(inputs, dailies),
    )


def value_history(
    inputs: Sequence[PositionInput], dailies: Mapping[int, pd.Series] | None = None
) -> pd.Series:
    """What today's positions were worth on every past day, at that day's prices.

    It starts on the day the last of the positions' items had its first sale: before that
    the portfolio as it is today could not have existed, and comparing with a handful of
    older items would be misleading. `dailies` holds daily price series already built.
    """
    columns = {}
    for p in inputs:
        daily = dailies.get(p.holding_id) if dailies else None
        if daily is None:
            if p.history.empty:
                continue
            daily = daily_series(p.history)["price"]
        price = daily.copy()
        price.index = pd.DatetimeIndex(price.index).normalize()
        columns[p.holding_id] = price * p.quantity
    if not columns:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(columns).sort_index()
    # Days on which every position already has a price.
    return frame.ffill().dropna().sum(axis=1)


# Valued portfolios by game filter (None = every game), with the fingerprint they match.
_cache: dict[int | None, tuple[tuple[object, ...], Portfolio]] = {}
_cache_lock = asyncio.Lock()


async def load_portfolio(sessions: SessionMaker, appid: int | None = None) -> Portfolio:
    """The valued portfolio, of one game or all, recomputed only when something changed.

    Results are kept in this process's memory; a cheap fingerprint query tells whether
    anything the valuation depends on has changed since.
    """
    async with _cache_lock:
        async with sessions() as session:
            fingerprint = await repo.portfolio_fingerprint(session)
        cached = _cache.get(appid)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        portfolio = await _compute_portfolio(sessions, appid)
        _cache[appid] = (fingerprint, portfolio)
        return portfolio


# Computed prices per item, with the (history stamp, rates, fee) they were computed from.
# While a big inventory loads, one item's history changes at a time; only that item is
# computed again.
_item_cache: dict[int, tuple[tuple[object, ...], ItemPrices]] = {}
# Items whose histories are read from the database and computed in one go; between batches
# the event loop serves the UI.
_BATCH = 25


async def _compute_portfolio(sessions: SessionMaker, appid: int | None = None) -> Portfolio:
    async with sessions() as session:
        holdings = [
            (holding, item)
            for holding, item in await repo.active_holdings(session)
            if appid is None or item.appid == appid
        ]
        fx = await repo.latest_fx(session)
        item_ids = list({item.id for _, item in holdings})
        stamps = await repo.history_stamps(session, item_ids)
    fees = {item.id: fee_rate(item) for _, item in holdings}
    rates = tuple(sorted(fx.items()))
    keys = {item_id: (stamps.get(item_id), rates, fees[item_id]) for item_id in item_ids}
    stale = [i for i in item_ids if i not in _item_cache or _item_cache[i][0] != keys[i]]
    for batch in batched(stale, _BATCH, strict=False):
        async with sessions() as session:
            points = await repo.load_histories(session, list(batch))
        # pandas work runs in a thread so that the UI stays responsive meanwhile.
        computed = await asyncio.to_thread(_compute_items, points, fx, fees)
        for item_id, prices in computed.items():
            _item_cache[item_id] = (keys[item_id], prices)
    inputs = [
        PositionInput(
            holding_id=holding.id,
            item_id=item.id,
            name=item.market_hash_name,
            source=holding.source,
            location=holding.location,
            quantity=holding.quantity,
            buy_price_usd=holding.buy_price_usd,
            history=_EMPTY,
            icon_url=item.icon_url,
            rarity=item.rarity,
            quality=item.quality,
            pinned=holding.pinned,
            appid=item.appid,
            name_color=item.name_color,
            publisher_fee=fees[item.id],
            prices=_item_cache[item.id][1],
            listing=listing_of(item),
        )
        for holding, item in holdings
    ]
    return await asyncio.to_thread(value_positions, inputs)


def _compute_items(
    points: Mapping[int, Sequence[repo.StoredPoint]],
    fx: Mapping[int, Decimal],
    fees: Mapping[int, float],
) -> dict[int, ItemPrices]:
    return {
        item_id: item_prices(_frame_or_empty(rows, fx), fees[item_id])
        for item_id, rows in points.items()
    }


_EMPTY = to_frame([], {})


def fee_rate(item: Item) -> float:
    """The publisher's share of the market fee for an item: its game's own, or the default."""
    return float(item.publisher_fee) if item.publisher_fee is not None else PUBLISHER_FEE_RATE


def _frame_or_empty(points: Sequence[repo.StoredPoint], fx: Mapping[int, Decimal]) -> pd.DataFrame:
    """Without Steam's rate for the currency yet, treat the item as unpriced for now."""
    try:
        return to_frame(points, fx)
    except MissingRateError:
        return to_frame([], fx)
