"""Portfolio valuation: positions priced in USD, totals and the value over time."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import batched

import numpy as np
import pandas as pd

from invinv.analysis import (
    CHANGE_WINDOWS,
    Metrics,
    MissingRateError,
    change_since,
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
    chart_excluded: bool = False
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
    daily: pd.Series | None  # robust daily price, for the portfolio's value over time
    robust: pd.Series | None  # robust prices, the reference for period changes
    trend: tuple[float, ...]
    sales_price: float | None  # pricing.recent_sales_price(), when nothing is listed


def item_prices(history: pd.DataFrame, publisher_fee: float = PUBLISHER_FEE_RATE) -> ItemPrices:
    views = price_views(history) if not history.empty else None
    return ItemPrices(
        sales_price=recent_sales_price(views) if views else None,
        metrics=compute_metrics(history, views, publisher_fee),
        daily=views.robust_daily if views else None,
        robust=views.robust if views else None,
        trend=tuple(recent_trend(history, views=views)),
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
    chart_excluded: bool = False
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
    # The item whose first sale starts value_history (the newest on the market), if any.
    newest_item: str | None = None
    newest_holding_id: int | None = None
    chart_excluded: int = 0  # positions left out of value_history


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
        changes = _changes(computed.robust, price, now)
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
                changes,
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
            change_24h=changes["24h"],
            change_7d=changes["7d"],
            change_30d=changes["30d"],
            icon_url=p.icon_url,
            rarity=p.rarity,
            quality=p.quality,
            trend=computed.trend,
            pinned=p.pinned,
            chart_excluded=p.chart_excluded,
            appid=p.appid,
            name_color=p.name_color,
            changes=tuple((key, changes[key]) for key in PERIODS),
            price_source=source,
        )
        for p, price, value, proceeds, cost, pnl, metrics, computed, source, changes in rows
    ]
    positions.sort(key=lambda pos: pos.value or 0, reverse=True)
    with_pnl = [pos for pos in positions if pos.pnl is not None]
    newest = _newest_item(inputs, dailies)
    return Portfolio(
        positions=positions,
        value=total,
        proceeds=sum(pos.proceeds for pos in positions if pos.proceeds is not None),
        cost=sum(pos.cost for pos in with_pnl if pos.cost is not None),
        pnl=sum(pos.pnl for pos in with_pnl if pos.pnl is not None),
        unpriced=sum(1 for pos in positions if pos.price is None),
        value_history=value_history(inputs, dailies),
        newest_item=newest[1] if newest else None,
        newest_holding_id=newest[0] if newest else None,
        chart_excluded=sum(1 for p in inputs if p.chart_excluded),
    )


def _newest_item(
    inputs: Sequence[PositionInput], dailies: Mapping[int, pd.Series]
) -> tuple[int, str] | None:
    """(holding id, name) of the item that came to the market last among those in the chart:
    value_history() starts on its first sale."""
    firsts = []
    for p in inputs:
        daily = dailies.get(p.holding_id)
        if daily is not None and not daily.empty and not p.chart_excluded:
            firsts.append((daily.index[0], p.holding_id, p.name))
    if not firsts:
        return None
    _, holding_id, name = max(firsts)
    return holding_id, name


def _changes(
    robust: pd.Series | None, price: float | None, now: datetime
) -> dict[str, float | None]:
    """Changes over the 24h / 7d / 30d columns and over every key of PERIODS."""
    return {
        key: change_since(robust, price, span, now)
        if robust is not None and price is not None
        else None
        for key, span in (CHANGE_WINDOWS | PERIODS).items()
    }


def value_history(
    inputs: Sequence[PositionInput], dailies: Mapping[int, pd.Series] | None = None
) -> pd.Series:
    """What today's positions were worth on every past day, at that day's prices.

    It starts on the day the last of the positions' items had its first sale: before that
    the portfolio as it is today could not have existed, and comparing with a handful of
    older items would be misleading. Positions excluded from the chart do not count.
    `dailies` holds daily price series already built.
    """
    columns: list[tuple[np.ndarray, np.ndarray]] = []
    for p in inputs:
        if p.chart_excluded:
            continue
        daily = dailies.get(p.holding_id) if dailies else None
        if daily is None:
            if p.history.empty:
                continue
            daily = daily_series(p.history)["price"]
        # Plain UTC datetime64: numpy turns timezone-aware dates into slow Python objects.
        days = pd.DatetimeIndex(daily.index).tz_convert(None).normalize().to_numpy()
        columns.append((days, daily.to_numpy(dtype=float) * p.quantity))
    if not columns:
        return pd.Series(dtype=float)
    # One matrix (day x position) instead of aligning hundreds of series one by one.
    all_days = np.unique(np.concatenate([days for days, _ in columns]))
    matrix = np.full((len(all_days), len(columns)), np.nan)
    for column, (days, values) in enumerate(columns):
        matrix[np.searchsorted(all_days, days), column] = values
    frame = pd.DataFrame(matrix, index=pd.DatetimeIndex(all_days).tz_localize(UTC))
    # Days on which every position already has a price.
    return frame.ffill().dropna().sum(axis=1)


# The valued portfolios, of every game together (None) and of each game, with the
# fingerprint they match: all of them are computed at once, so switching games is instant.
_cache: tuple[tuple[object, ...], dict[int | None, Portfolio]] | None = None
_cache_lock = asyncio.Lock()


async def load_portfolio(sessions: SessionMaker, appid: int | None = None) -> Portfolio:
    """The valued portfolio, of one game or all, recomputed only when something changed.

    Results are kept in this process's memory; a cheap fingerprint query tells whether
    anything the valuation depends on has changed since.
    """
    return (await load_portfolios(sessions)).get(appid) or _empty_portfolio()


async def load_portfolios(sessions: SessionMaker) -> dict[int | None, Portfolio]:
    """load_portfolio() of every game and of all of them, from one fingerprint query."""
    global _cache
    async with _cache_lock:
        async with sessions() as session:
            fingerprint = await repo.portfolio_fingerprint(session)
        if _cache is not None and _cache[0] == fingerprint:
            return _cache[1]
        portfolios = await _compute_portfolios(sessions)
        _cache = (fingerprint, portfolios)
        return portfolios


def _empty_portfolio() -> Portfolio:
    return value_positions([])


# Computed prices per item, with the (history stamp, rates, fee) they were computed from.
# While a big inventory loads, one item's history changes at a time; only that item is
# computed again.
_item_cache: dict[int, tuple[tuple[object, ...], ItemPrices]] = {}
# Items whose histories are read from the database and computed in one go; between batches
# the event loop serves the UI.
_BATCH = 25


async def cached_item_prices(
    sessions: SessionMaker, items: Sequence[Item]
) -> dict[int, ItemPrices]:
    """ItemPrices of the items, computed again only for those whose history, the rates or
    the fee changed since."""
    async with sessions() as session:
        fx = await repo.latest_fx(session)
        item_ids = list({item.id for item in items})
        stamps = await repo.history_stamps(session, item_ids)
    fees = {item.id: fee_rate(item) for item in items}
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
    return {item_id: _item_cache[item_id][1] for item_id in item_ids}


async def _compute_portfolios(sessions: SessionMaker) -> dict[int | None, Portfolio]:
    async with sessions() as session:
        holdings = await repo.active_holdings(session)
    prices = await cached_item_prices(sessions, [item for _, item in holdings])
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
            chart_excluded=holding.chart_excluded,
            appid=item.appid,
            name_color=item.name_color,
            publisher_fee=fee_rate(item),
            prices=prices[item.id],
            listing=listing_of(item),
        )
        for holding, item in holdings
    ]
    return await asyncio.to_thread(_value_by_game, inputs)


def _value_by_game(inputs: Sequence[PositionInput]) -> dict[int | None, Portfolio]:
    games = sorted({p.appid for p in inputs})
    result: dict[int | None, Portfolio] = {None: value_positions(inputs)}
    for appid in games:
        result[appid] = value_positions([p for p in inputs if p.appid == appid])
    return result


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
