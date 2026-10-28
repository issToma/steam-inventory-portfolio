from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cs2market.analysis import to_frame
from cs2market.db.repo import StoredPoint
from cs2market.portfolio import PositionInput, value_positions

DAY = datetime(2026, 10, 1, tzinfo=UTC)


def history(*prices: float) -> list[StoredPoint]:
    return [
        StoredPoint(DAY + timedelta(days=i), "day", Decimal(str(p)), 1, 1)
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
        quantity=quantity,
        buy_price_usd=Decimal(buy) if buy else None,
        history=to_frame(history(*prices), {}),
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
