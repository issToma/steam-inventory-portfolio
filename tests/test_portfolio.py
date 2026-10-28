from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from invinv.analysis import to_frame
from invinv.db.repo import StoredPoint
from invinv.portfolio import PositionInput, period_stats, value_positions
from invinv.pricing import Listing

DAY = datetime(2026, 10, 1, tzinfo=UTC)


def history(*prices: float) -> list[StoredPoint]:
    return [
        StoredPoint(DAY + timedelta(days=i), "day", Decimal(str(p)), 20, 1)
        for i, p in enumerate(prices)
    ]


def position(
    holding_id: int, prices: list[float], quantity: int, buy: str | None = None
) -> PositionInput:
    return PositionInput(
        holding_id=holding_id,
        item_id=holding_id,
        name=f"item {holding_id}",
        source="manual",
        location=None,
        appid=730,
        quantity=quantity,
        buy_price_usd=Decimal(buy) if buy else None,
        history=to_frame(history(*prices), {}),
        # Listed now at the last price.
        listing=Listing(Decimal(str(prices[-1])), None, None, datetime.now(UTC))
        if prices
        else None,
    )


def test_totals_shares_and_pnl() -> None:
    portfolio = value_positions(
        [position(1, [10, 20], quantity=2, buy="15"), position(2, [5], quantity=4)]
    )

    assert portfolio.value == 60
    first, second = portfolio.positions
    assert first.holding_id == 1
    assert first.value == 40
    assert first.share == pytest.approx(2 / 3)
    # A $20 sale leaves $17.39 after fees (at $17.40 the buyer would pay $20.01).
    assert first.pnl == pytest.approx(2 * 17.39 - 2 * 15)
    assert second.pnl is None
    assert portfolio.cost == 30
    assert portfolio.pnl == pytest.approx(4.78)


def test_unpriced_positions_are_counted_not_valued() -> None:
    portfolio = value_positions(
        [position(1, [10], quantity=1), position(2, [], quantity=3, buy="1")]
    )

    assert portfolio.value == 10
    assert portfolio.unpriced == 1
    assert portfolio.cost == 0


def test_value_history_uses_todays_quantities() -> None:
    portfolio = value_positions([position(1, [10, 20, 30], quantity=2), position(2, [0, 5], 1)])

    assert list(portfolio.value_history) == [20, 45, 65]


def test_period_stats() -> None:
    history = value_positions([position(1, [10, 30, 5, 20], quantity=1)]).value_history

    stats = period_stats(history, timedelta(days=2))
    everything = period_stats(history, None)

    assert stats is not None
    assert (stats.start, stats.end, stats.change) == (30, 20, -10)
    assert (stats.high, stats.low) == (30, 5)
    assert stats.low_on == pd.Timestamp(DAY + timedelta(days=2))
    assert everything is not None
    assert everything.change_pct == pytest.approx(1.0)


def test_position_change_per_period() -> None:
    now = DAY + timedelta(days=2, hours=1)
    (pos,) = value_positions([position(1, [10, 20, 40], quantity=1)], now=now).positions

    assert pos.change("7D") == pytest.approx(3.0)
    assert pos.change("All") == pytest.approx(3.0)


def test_value_history_starts_when_every_item_is_on_the_market() -> None:
    older = position(1, [10, 10, 10, 10], quantity=1)
    newer = PositionInput(
        holding_id=2,
        item_id=2,
        name="item 2",
        source="manual",
        location=None,
        appid=730,
        quantity=1,
        buy_price_usd=None,
        history=to_frame(
            [
                StoredPoint(DAY + timedelta(days=2 + i), "day", Decimal(p), 20, 1)
                for i, p in enumerate(["5", "6"])
            ],
            {},
        ),
    )

    history = value_positions([older, newer]).value_history

    assert list(history) == [15, 16]
    assert history.index[0] == pd.Timestamp(DAY + timedelta(days=2))


def test_price_is_the_cheapest_listing_not_an_odd_last_sale() -> None:
    odd = PositionInput(
        holding_id=1,
        item_id=1,
        name="loading screen",
        source="inventory",
        location=None,
        appid=570,
        quantity=1,
        buy_price_usd=None,
        history=to_frame(history(0.03, 0.03, 0.03, 32.0, 0.03, 15.0), {}),
        listing=Listing(Decimal("0.03"), None, None, datetime.now(UTC)),
    )

    (position_,) = value_positions([odd]).positions

    assert (position_.price, position_.price_source) == (0.03, "listing")


def test_without_listings_the_price_is_the_median_of_recent_sales() -> None:
    stale = datetime.now(UTC) - timedelta(days=10)
    no_listing = PositionInput(
        holding_id=1,
        item_id=1,
        name="loading screen",
        source="inventory",
        location=None,
        appid=570,
        quantity=1,
        buy_price_usd=None,
        history=to_frame(history(0.03, 0.03, 0.03, 32.0, 0.03, 15.0), {}),
        listing=Listing(Decimal("9"), None, None, stale),  # too old to trust
    )

    (position_,) = value_positions([no_listing]).positions

    assert (position_.price, position_.price_source) == (0.03, "sales")
