"""Portfolio valuation: positions priced in USD, totals and the value over time."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from cs2market.analysis import (
    MissingRateError,
    compute_metrics,
    daily_series,
    recent_trend,
    to_frame,
)
from cs2market.core.fees import seller_proceeds
from cs2market.db import repo
from cs2market.db.session import SessionMaker


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


def value_positions(inputs: Sequence[PositionInput]) -> Portfolio:
    rows = []
    for p in inputs:
        metrics = compute_metrics(p.history)
        price = metrics.last_price if metrics else None
        unit_proceeds = seller_proceeds(round(price * 100)) / 100 if price is not None else None
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
            trend=tuple(recent_trend(p.history)),
        )
        for p, price, value, proceeds, cost, pnl, metrics in rows
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
        value_history=value_history(inputs),
    )


def value_history(inputs: Sequence[PositionInput]) -> pd.Series:
    """What today's positions were worth on every past day, at that day's prices.

    Before an item's first sale on record it contributes nothing.
    """
    columns = {}
    for p in inputs:
        if p.history.empty:
            continue
        price = daily_series(p.history)["price"]
        price.index = pd.DatetimeIndex(price.index).normalize()
        columns[p.holding_id] = price * p.quantity
    if not columns:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(columns).sort_index()
    return frame.ffill().fillna(0).sum(axis=1)


async def load_portfolio(sessions: SessionMaker) -> Portfolio:
    """Read the active positions with their full history and value them."""
    async with sessions() as session:
        holdings = await repo.active_holdings(session)
        fx = await repo.latest_fx(session)
        histories: dict[int, pd.DataFrame] = {}
        for _, item in holdings:
            if item.id not in histories:
                histories[item.id] = _frame_or_empty(await repo.load_history(session, item.id), fx)
    return value_positions(
        [
            PositionInput(
                holding_id=holding.id,
                item_id=item.id,
                name=item.market_hash_name,
                source=holding.source,
                location=holding.location,
                quantity=holding.quantity,
                buy_price_usd=holding.buy_price_usd,
                history=histories[item.id],
                icon_url=item.icon_url,
                rarity=item.rarity,
                quality=item.quality,
            )
            for holding, item in holdings
        ]
    )


def _frame_or_empty(points: Sequence[repo.StoredPoint], fx: Mapping[int, Decimal]) -> pd.DataFrame:
    """Without Steam's rate for the currency yet, treat the item as unpriced for now."""
    try:
        return to_frame(points, fx)
    except MissingRateError:
        return to_frame([], fx)
