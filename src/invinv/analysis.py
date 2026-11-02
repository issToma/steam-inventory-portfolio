"""Price series and metrics in USD, built from stored history with pandas."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from invinv.core.currency import USD
from invinv.db.repo import StoredPoint


class MissingRateError(Exception):
    """Prices are in a currency we have no Steam rate for yet."""

    def __init__(self, currency: int) -> None:
        super().__init__(f"no exchange rate for currency {currency} yet")
        self.currency = currency


@dataclass(frozen=True)
class Metrics:
    last_price: float  # of the last sale; the current price is pricing.quote()
    last_ts: pd.Timestamp
    volatility_30d: float | None
    avg_daily_volume_30d: float | None


def to_frame(
    points: Sequence[StoredPoint], fx: Mapping[int, Decimal], *, drop_outliers: bool = True
) -> pd.DataFrame:
    """Columns: ts (UTC), resolution, price (USD), volume.

    Every stored price was converted by Steam at the time of the last fetch, so all of them
    are converted back with the latest Steam rate (docs/SPEC.md, section 4.2). Outliers
    (see remove_outliers) are dropped unless asked otherwise.
    """
    rates: dict[int, float] = {USD: 1.0}
    for currency in {p.currency for p in points} - {USD}:
        if currency not in fx:
            raise MissingRateError(currency)
        rates[currency] = float(fx[currency])
    frame = pd.DataFrame(
        {
            "ts": [p.ts for p in points],
            "resolution": [p.resolution for p in points],
            "price": [float(p.price) * rates[p.currency] for p in points],
            "volume": [p.volume for p in points],
        },
        columns=["ts", "resolution", "price", "volume"],
    )
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return remove_outliers(frame) if drop_outliers else frame


# Compared with its neighbours of the same resolution, a point is an outlier when it is far
# outside their usual spread (Hampel filter: median absolute deviation scaled to a standard
# deviation) and either rests on very few sales and is 30% off their median, or is several
# times off it whatever the number of sales: Steam's data has days where a cheap skin's
# median jumps 100x on dozens of odd trades.
OUTLIER_MAX_SALES = 2
OUTLIER_MIN_DEVIATION = 0.30
OUTLIER_EXTREME_RATIO = 4.0
OUTLIER_MAD_SCALE = 3.0
OUTLIER_WINDOW = 11


def outlier_mask(frame: pd.DataFrame) -> pd.Series:
    """True for single odd sales (such as a sticker craft) and for wildly off days."""
    mask = pd.Series(False, index=frame.index)
    for _, group in frame.groupby("resolution"):
        prices = group.sort_values("ts")["price"]
        rolling = prices.rolling(OUTLIER_WINDOW, center=True, min_periods=5)
        median = rolling.median()
        mad = (prices - median).abs().rolling(OUTLIER_WINDOW, center=True, min_periods=5).median()
        deviation = (prices - median).abs()
        few_sales = group.loc[prices.index, "volume"] <= OUTLIER_MAX_SALES
        ratio = (prices / median).where(prices >= median, median / prices)
        odd = (deviation > OUTLIER_MAD_SCALE * 1.4826 * mad) & (
            (few_sales & (deviation > OUTLIER_MIN_DEVIATION * median))
            | (ratio > OUTLIER_EXTREME_RATIO)
        )
        mask.loc[odd.index] = odd.fillna(False)
    return mask


def remove_outliers(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[~outlier_mask(frame)].reset_index(drop=True)


def hourly_series(frame: pd.DataFrame) -> pd.DataFrame:
    hourly = frame[frame["resolution"] == "hour"]
    return hourly.set_index("ts")[["price", "volume"]].sort_index()


def daily_series(frame: pd.DataFrame, hourly: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per day: Steam's daily points, plus hourly points aggregated for the days
    Steam only has hourly data for (price is the volume-weighted median, so one odd trade
    cannot move the day; volume is summed). `hourly` is hourly_series(frame) if built."""
    daily = frame[frame["resolution"] == "day"].set_index("ts")[["price", "volume"]]
    if hourly is None:
        hourly = hourly_series(frame)
    if not hourly.empty:
        volume = hourly["volume"].resample("D").sum()
        from_hourly = pd.DataFrame(
            {"price": _daily_weighted_median(hourly), "volume": volume}
        ).dropna()
        from_hourly = from_hourly[from_hourly["volume"] > 0]
        from_hourly = from_hourly[~from_hourly.index.isin(daily.index)]
        daily = pd.concat([daily, from_hourly])
    return daily.sort_index()


def _daily_weighted_median(hourly: pd.DataFrame) -> pd.Series:
    """Per day, the price at which half of the day's sales were cheaper."""
    points = hourly.assign(day=pd.DatetimeIndex(hourly.index).floor("D"))
    points = points.sort_values(["day", "price"])
    by_day = points.groupby("day")["volume"]
    reached = by_day.cumsum() >= by_day.transform("sum") / 2
    median = points[reached].groupby("day")["price"].first()
    median.index = pd.DatetimeIndex(median.index)
    return median


@dataclass(frozen=True)
class PriceViews:
    """The series derived from one history frame, built once and shared by the metrics."""

    daily: pd.DataFrame
    hourly: pd.DataFrame
    prices: pd.Series  # daily points up to the first hourly one, then hourly points
    # robust_prices() of `prices` and of `daily`: what charts and period changes use.
    robust: pd.Series
    robust_daily: pd.Series


def price_views(frame: pd.DataFrame) -> PriceViews:
    hourly = hourly_series(frame)
    daily = daily_series(frame, hourly)
    combined = daily
    robust = robust_prices(daily)
    if not hourly.empty:
        before = daily[daily.index < hourly.index[0]]
        combined = pd.concat([before, hourly])
        # Each part gets its own window: a daily point holds far more sales than an hourly
        # one, and one window for both left the hourly points without any smoothing.
        robust = pd.concat([robust_prices(before), robust_prices(hourly)])
    return PriceViews(
        daily=daily,
        hourly=hourly,
        prices=combined["price"],
        robust=robust,
        robust_daily=robust_prices(daily),
    )


# A price that one odd trade cannot move: the median over about the last ROBUST_SALES sales.
ROBUST_SALES = 20
MAX_ROBUST_POINTS = 30


def robust_prices(series: pd.DataFrame) -> pd.Series:
    """Every point's price as the median of the points that hold its last 20 sales.

    The window is counted in sales at each point: a busy day of a liquid item stands on its
    own, while a day with a single sale (a quiet stretch, or today so far) reaches back to the
    days before it, up to MAX_ROBUST_POINTS points.
    """
    prices = series["price"].astype(float)
    if series.empty:
        return prices
    sold = np.cumsum(series["volume"].to_numpy(dtype=float))
    # The first point of each window: the latest one after which ROBUST_SALES sales remain.
    first = np.searchsorted(sold, sold - ROBUST_SALES, side="right")
    lengths = np.clip(np.arange(len(sold)) - first + 1, 1, MAX_ROBUST_POINTS)
    # Odd lengths, so that the median is one of the prices rather than the mean of two: a
    # quiet day next to a busy one must not land halfway between them.
    lengths = lengths + (lengths % 2 == 0)
    robust = np.empty(len(sold))
    # One rolling median per window length: a handful of lengths, each a fast pandas pass.
    for length in np.unique(lengths):
        rolled = prices.rolling(int(length), min_periods=1).median().to_numpy()
        robust[lengths == length] = rolled[lengths == length]
    return pd.Series(robust, index=series.index, name="price")


# Windows of the change columns (24h / 7d / 30d).
CHANGE_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def compare_line(views: PriceViews, period: timedelta | None, now: datetime) -> pd.Series:
    """The robust price over the period as a percentage of where it stood at its start.

    Starts at 100 at the start of the period (or of the history, if shorter) and runs to
    `now`, so items that did not sell lately still span the whole period.
    """
    # Hourly points are noisy beyond a week: longer periods read better daily.
    short = period is not None and period <= timedelta(days=7)
    return normalized_line(views.robust if short else views.robust_daily, period, now)


def normalized_line(robust: pd.Series, period: timedelta | None, now: datetime) -> pd.Series:
    """compare_line() of a given robust series."""
    if robust.empty:
        return robust
    index = pd.DatetimeIndex(robust.index)
    start = index[0] if period is None else max(index[0], pd.Timestamp(now - period))
    position = max(int(index.searchsorted(start, side="right")) - 1, 0)
    base = float(robust.iloc[position])
    if not base:
        return robust.iloc[0:0]
    line = pd.concat(
        [
            pd.Series([base], index=pd.DatetimeIndex([start])),
            robust[index > start],
        ]
    )
    line.loc[pd.Timestamp(now)] = float(line.iloc[-1])
    percent: pd.Series = line / base * 100
    return percent


def change_since(
    robust: pd.Series, price: float, period: timedelta | None, now: datetime
) -> float | None:
    """How the current price compares with the robust price at the start of the period
    (None = all history; a history shorter than the period counts from its first point)."""
    if robust.empty:
        return None
    position = 0
    if period is not None:
        # The last point at or before the start of the period (a binary search).
        index = pd.DatetimeIndex(robust.index)
        position = max(int(index.searchsorted(pd.Timestamp(now - period), side="right")) - 1, 0)
    then = float(robust.iloc[position])
    return price / then - 1 if then else None


def compute_metrics(frame: pd.DataFrame, views: PriceViews | None = None) -> Metrics | None:
    if frame.empty:
        return None
    views = views or price_views(frame)
    daily = views.daily
    # Finest data available for every period: daily points up to the first hourly one.
    recent = views.prices
    last_ts = recent.index[-1]
    last_price = float(recent.iloc[-1])

    month = daily[daily.index > last_ts - timedelta(days=30)]
    returns = month["price"].pipe(lambda s: (s / s.shift(1)).map(math.log)).dropna()
    return Metrics(
        last_price=last_price,
        last_ts=last_ts,
        volatility_30d=float(returns.std()) if len(returns) > 1 else None,
        avg_daily_volume_30d=float(month["volume"].mean()) if not month.empty else None,
    )


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def recent_trend(
    frame: pd.DataFrame, days: int = 7, bucket: str = "6h", views: PriceViews | None = None
) -> list[float]:
    """Robust prices of the last few days, averaged per bucket, for a small trend line."""
    if frame.empty:
        return []
    series = (views or price_views(frame)).robust
    recent = series[series.index >= series.index[-1] - timedelta(days=days)]
    return [float(v) for v in recent.resample(bucket).mean().dropna()]
