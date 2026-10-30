from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from invinv.db.tables import Item
from invinv.market import MarketRow, indices, segment_of

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def row(item_id: int, prices: list[float], segment: str = "Immortal") -> MarketRow:
    days = pd.date_range(NOW - timedelta(days=len(prices) - 1), periods=len(prices), freq="D")
    return MarketRow(
        item=Item(id=item_id, appid=570, market_hash_name=f"item {item_id}"),
        segment=segment,
        price=prices[-1],
        price_source="listing",
        changes={},
        sales_per_day=None,
        robust_daily=pd.Series(prices, index=days.normalize()),
    )


def test_index_is_the_median_item_of_a_segment() -> None:
    rows = [row(1, [10, 12]), row(2, [1, 1]), row(3, [4, 200]), row(4, [5, 50], "Rare")]

    (index,) = indices(rows, timedelta(days=7), NOW)

    # +20%, 0% and +4900%: the median is +20%, the jump does not carry the segment.
    # "Rare" has a single item and makes no index.
    assert (index.appid, index.segment, index.items) == (570, "Immortal", 3)
    assert index.change == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("rarity", "item_type", "expected"),
    [
        ("Covert", "★ Covert Knife", "Covert"),
        (None, "Immortal Wearable", "Immortal"),
        (None, "Foil Trading Card", "Foil"),
        (None, "Need for Speed Trading Card", "Trading Card"),
        (None, "Hoodie", None),
    ],
)
def test_segment(rarity: str | None, item_type: str, expected: str | None) -> None:
    assert segment_of(Item(rarity=rarity, item_type=item_type)) == expected
