from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from invinv.analysis import (
    MissingRateError,
    compute_metrics,
    daily_series,
    recent_trend,
    to_frame,
)
from invinv.core.parsers import Resolution
from invinv.db.repo import StoredPoint

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


def spiky(volume: int) -> list[StoredPoint]:
    prices = ["10", "10.2", "9.9", "10.1", "10", "30", "10.1", "9.8", "10", "10.2", "10"]
    return [point(START + timedelta(days=i), p, volume=volume) for i, p in enumerate(prices)]


def test_single_odd_sale_is_dropped() -> None:
    frame = to_frame(spiky(volume=1), {})

    assert len(frame) == 10
    assert frame["price"].max() < 11


def test_spike_backed_by_many_sales_is_kept() -> None:
    assert len(to_frame(spiky(volume=50), {})) == 11


def test_outliers_can_be_kept_on_request() -> None:
    assert len(to_frame(spiky(volume=1), {}, drop_outliers=False)) == 11


def test_real_trend_is_not_an_outlier() -> None:
    # A steady climb with single sales: each point is close to its neighbours.
    points = [point(START + timedelta(days=i), str(10 + i), volume=1) for i in range(20)]

    assert len(to_frame(points, {})) == 20


def test_wild_day_is_dropped_even_with_many_sales() -> None:
    prices = ["0.03", "0.04", "0.03", "0.03", "0.04", "60", "0.03", "0.04", "0.03", "0.03", "0.04"]
    points = [point(START + timedelta(days=i), p, volume=57) for i, p in enumerate(prices)]

    frame = to_frame(points, {})

    assert frame["price"].max() < 1
