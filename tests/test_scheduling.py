from datetime import UTC, datetime, timedelta

import pytest

from invinv import scheduling
from invinv.scheduling import (
    DAY,
    SIX_HOURS,
    Importance,
    refresh_interval,
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
