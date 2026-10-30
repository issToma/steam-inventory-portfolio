from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from invinv.analysis import (
    MissingRateError,
    change_since,
    compare_line,
    compute_metrics,
    daily_series,
    price_views,
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

    # Day 2: one sale at 10 and three at 20; the weighted median is 20.
    assert list(daily["price"]) == [10, 20]
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
    # 20 sales per point: a liquid item, whose robust line is its plain prices.
    points = [point(START + timedelta(days=d), "1", volume=20) for d in range(20)]
    points += [
        point(START + timedelta(days=20, hours=h), str(10 + h // 6), 20, resolution="hour")
        for h in range(12)
    ]

    trend = recent_trend(to_frame(points, {}))

    assert trend[-2:] == [10, 11]
    assert set(trend[:-2]) == {1}
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


def test_robust_prices_ignore_odd_trades_of_a_rare_item() -> None:
    prices = ["0.03"] * 10 + ["32", "0.03", "15"]
    frame = to_frame(
        [point(START + timedelta(days=i), p, volume=1) for i, p in enumerate(prices)], {}
    )

    robust = price_views(frame).robust

    assert robust.iloc[-1] == pytest.approx(0.03)


def test_change_since_compares_the_price_with_the_robust_price_then() -> None:
    frame = to_frame(
        [point(START + timedelta(days=i), "10", volume=20) for i in range(10)]
        + [point(START + timedelta(days=10), "12", volume=20)],
        {},
    )
    robust = price_views(frame).robust
    now = START + timedelta(days=10, hours=1)

    assert change_since(robust, 15.0, timedelta(days=7), now) == pytest.approx(0.5)
    assert change_since(robust, 15.0, None, now) == pytest.approx(0.5)


def test_compare_line_starts_at_100_and_reaches_now() -> None:
    frame = to_frame(
        [point(START + timedelta(days=i), str(10 + i), volume=20) for i in range(10)], {}
    )
    now = START + timedelta(days=12)

    line = compare_line(price_views(frame), timedelta(days=5), now)

    assert line.iloc[0] == 100
    # Day 7 (start of the period) was 17; the last sale, day 9, was 19.
    assert line.iloc[-1] == pytest.approx(19 / 17 * 100)
    assert line.index[-1] == pd.Timestamp(now)


def test_hourly_points_are_smoothed_on_their_own_scale() -> None:
    # Years of busy days, then an hour-by-hour month with a few sales per hour and one odd
    # trade: the hourly window must still be wide enough to ignore it.
    points = [point(START - timedelta(days=d), "30", volume=80) for d in range(60, 0, -1)]
    hours = ["31", "32", "31", "40", "31", "32", "31", "32", "31"]
    points += [
        point(START + timedelta(hours=h), price, volume=3, resolution="hour")
        for h, price in enumerate(hours)
    ]

    robust = price_views(to_frame(points, {})).robust

    assert robust[START + timedelta(hours=3)] < 33
