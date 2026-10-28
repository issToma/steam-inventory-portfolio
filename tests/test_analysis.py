from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cs2market.analysis import (
    MissingRateError,
    compute_metrics,
    daily_series,
    recent_trend,
    to_frame,
)
from cs2market.core.parsers import Resolution
from cs2market.db.repo import StoredPoint

START = datetime(2026, 9, 1, tzinfo=UTC)


def point(
    ts: datetime, price: str, volume: int = 1, resolution: Resolution = "day", currency: int = 1
) -> StoredPoint:
    return StoredPoint(ts, resolution, Decimal(price), volume, currency)


def test_to_frame_converts_with_steam_rate() -> None:
    frame = to_frame([point(START, "10", currency=3)], {3: Decimal("1.12")})

    assert frame["price"].iloc[0] == pytest.approx(11.2)


def test_to_frame_without_rate() -> None:
    with pytest.raises(MissingRateError):
        to_frame([point(START, "10", currency=3)], {})


def test_daily_series_prefers_steam_daily_points_and_aggregates_hourly_ones() -> None:
    day2 = START + timedelta(days=1)
    frame = to_frame(
        [
            point(START, "10", volume=5),
            point(START, "99", volume=1, resolution="hour"),
            point(day2, "10", volume=1, resolution="hour"),
            point(day2 + timedelta(hours=1), "20", volume=3, resolution="hour"),
        ],
        {},
    )

    daily = daily_series(frame)

    assert list(daily["price"]) == [10, 17.5]
    assert list(daily["volume"]) == [5, 4]


def test_metrics() -> None:
    points = [point(START + timedelta(days=d), str(10 + d)) for d in range(31)]
    points += [
        point(START + timedelta(days=31, hours=h), "41", volume=2, resolution="hour")
        for h in range(25)
    ]

    metrics = compute_metrics(to_frame(points, {}))

    assert metrics is not None
    assert metrics.last_price == 41
    assert metrics.change_24h == 0
    assert metrics.change_7d == pytest.approx(41 / 35 - 1)
    assert metrics.change_30d == pytest.approx(41 / 12 - 1)
    assert metrics.proceeds_after_fee == pytest.approx(35.66)


def test_metrics_of_empty_history() -> None:
    assert compute_metrics(to_frame([], {})) is None


def test_recent_trend_averages_the_last_week() -> None:
    points = [point(START + timedelta(days=d), "1") for d in range(20)]
    points += [
        point(START + timedelta(days=20, hours=h), str(10 + h // 6), resolution="hour")
        for h in range(12)
    ]

    assert recent_trend(to_frame(points, {})) == [10, 11]
    assert recent_trend(to_frame([], {})) == []
