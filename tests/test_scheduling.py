from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from invinv import scheduling
from invinv.core.parsers import HistoryPoint, PriceHistory
from invinv.scheduling import (
    DAY,
    SIX_HOURS,
    Importance,
    held_value_usd,
    refresh_interval,
    sales_per_day,
)

HOUR = timedelta(hours=1)
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("importance", "expected"),
    [
        (Importance(watched=True, held_value_usd=0.5, sales_per_day=0), HOUR),
        (Importance(watched=False, held_value_usd=None, sales_per_day=0), HOUR),
        (Importance(watched=False, held_value_usd=150, sales_per_day=40), HOUR),
        (Importance(watched=False, held_value_usd=5, sales_per_day=40), SIX_HOURS),
        (Importance(watched=False, held_value_usd=0.8, sales_per_day=500), DAY),
        # Valuable but rarely sold: the price hardly moves between hourly refreshes.
        (Importance(watched=False, held_value_usd=900, sales_per_day=0.2), SIX_HOURS),
    ],
)
def test_refresh_interval(importance: Importance, expected: timedelta) -> None:
    assert refresh_interval(importance, base=HOUR) == expected


def test_interval_is_never_shorter_than_base() -> None:
    important = Importance(watched=True, held_value_usd=None, sales_per_day=100)

    assert refresh_interval(important, base=2 * HOUR) == 2 * HOUR


def history(currency: int, *points: tuple[datetime, str, int]) -> PriceHistory:
    return PriceHistory(
        currency, [HistoryPoint(ts, "hour", Decimal(price), volume) for ts, price, volume in points]
    )


def test_sales_per_day_counts_the_last_30_days() -> None:
    h = history(1, (NOW - timedelta(days=40), "1", 3000), (NOW - HOUR, "1", 60))

    assert sales_per_day(h, NOW) == 2


def test_held_value_converts_currency() -> None:
    eur = history(3, (NOW, "10", 1))
    usd = history(1, (NOW, "2", 1))

    assert held_value_usd([(2, eur), (3, usd)], {3: Decimal("1.1")}) == pytest.approx(28)
    assert held_value_usd([(1, eur)], {}) is None
    assert held_value_usd([(5, history(1))], {}) == 0


@pytest.mark.parametrize(
    ("item_type", "watched", "expected"),
    [
        ("Consumer Grade Pistol", True, scheduling.WATCHED_PRIORITY),
        ("★ Covert Knife", False, 9),
        ("StatTrak™ Classified Rifle", False, 7),
        ("Immortal Wearable", False, 8),
        ("Common Wearable", False, 3),
        ("Uncommon Bundle", False, 4),
        ("Foil Trading Card", False, 5),
        ("Need for Speed Trading Card", False, scheduling.UNKNOWN_PRIORITY),
        (None, False, scheduling.UNKNOWN_PRIORITY),
    ],
)
def test_fetch_priority(item_type: str | None, watched: bool, expected: int) -> None:
    assert scheduling.fetch_priority(item_type, watched=watched) == expected
