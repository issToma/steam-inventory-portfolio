from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from invinv.analysis import price_views, to_frame
from invinv.db.repo import StoredPoint
from invinv.pricing import Listing, Quote, quote, recent_sales_price, weighted_median

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def hourly(*sales: tuple[str, int]) -> list[StoredPoint]:
    return [
        StoredPoint(NOW - timedelta(hours=len(sales) - i), "hour", Decimal(price), volume, 1)
        for i, (price, volume) in enumerate(sales)
    ]


def test_weighted_median() -> None:
    assert weighted_median(np.array([1.0, 100.0, 2.0]), np.array([1, 1, 5])) == 2.0


def test_recent_sales_price_uses_the_last_sales_only() -> None:
    # Long ago it sold for 1; the last 20 sales were at 5, apart from one odd trade.
    points = hourly(("1", 50), ("5", 10), ("90", 1), ("5", 9))

    assert recent_sales_price(price_views(to_frame(points, {}))) == 5


def test_quote_prefers_a_fresh_listing() -> None:
    fresh = Listing(Decimal("2.5"), None, None, NOW - timedelta(hours=1))
    empty = Listing(None, None, None, NOW - timedelta(hours=1))
    old = Listing(Decimal("2.5"), None, None, NOW - timedelta(days=4))

    assert quote(fresh, 3.0, NOW) == Quote(2.5, "listing")
    assert quote(empty, 3.0, NOW) == Quote(3.0, "sales")
    assert quote(old, 3.0, NOW) == Quote(3.0, "sales")
    assert quote(None, None, NOW) is None
