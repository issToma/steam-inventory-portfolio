"""Price series and metrics in USD, built from stored history with pandas."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pandas as pd

from invinv.core.currency import USD
from invinv.core.fees import PUBLISHER_FEE_RATE, seller_proceeds
from invinv.db.repo import StoredPoint


class MissingRateError(Exception):
    """Prices are in a currency we have no Steam rate for yet."""

    def __init__(self, currency: int) -> None:
        super().__init__(f"no exchange rate for currency {currency} yet")
        self.currency = currency


@dataclass(frozen=True)
class Metrics:
    last_price: float
    last_ts: pd.Timestamp
    change_24h: float | None
    change_7d: float | None
    change_30d: float | None
    volatility_30d: float | None
    avg_daily_volume_30d: float | None
    proceeds_after_fee: float


def to_frame(
    points: Sequence[StoredPoint], fx: Mapping[int, Decimal], *, drop_outliers: bool = True
) -> pd.DataFrame:
    """Columns: ts (UTC), resolution, price (USD), volume.

    Every stored price was converted by Steam at the time of the last fetch, so all of them
    are converted back with the latest Steam rate (docs/SPEC.md, section 4.2). Outliers
    (see remove_outliers) are dropped unless asked otherwise.
    """
    rows = []
    for p in points:
        if p.currency == USD:
            rate = Decimal(1)
        elif p.currency in fx:
            rate = fx[p.currency]
        else:
            raise MissingRateError(p.currency)
        rows.append((p.ts, p.resolution, float(p.price * rate), p.volume))
    frame = pd.DataFrame(rows, columns=["ts", "resolution", "price", "volume"])
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


def daily_series(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per day: Steam's daily points, plus hourly points aggregated for the days
    Steam only has hourly data for (price is volume-weighted, volume is summed)."""
    daily = frame[frame["resolution"] == "day"].set_index("ts")[["price", "volume"]]
    hourly = hourly_series(frame)
    if not hourly.empty:
        weighted = (hourly["price"] * hourly["volume"]).resample("D").sum()
        volume = hourly["volume"].resample("D").sum()
        from_hourly = pd.DataFrame({"price": weighted / volume, "volume": volume}).dropna()
        from_hourly = from_hourly[~from_hourly.index.isin(daily.index)]
        daily = pd.concat([daily, from_hourly])
    return daily.sort_index()


def _change(series: pd.Series, last_ts: pd.Timestamp, period: timedelta) -> float | None:
    before = series[: last_ts - period]
    if before.empty or before.iloc[-1] == 0:
        return None
    return float(series.iloc[-1] / before.iloc[-1] - 1)


@dataclass(frozen=True)
class PriceViews:
    """The series derived from one history frame, built once and shared by the metrics."""

    daily: pd.DataFrame
    hourly: pd.DataFrame
    prices: pd.Series  # combined_prices()


def price_views(frame: pd.DataFrame) -> PriceViews:
    daily = daily_series(frame)
    hourly = hourly_series(frame)
    prices = daily["price"]
    if not hourly.empty:
        prices = pd.concat([prices[prices.index < hourly.index[0]], hourly["price"]])
    return PriceViews(daily=daily, hourly=hourly, prices=prices)


def compute_metrics(
    frame: pd.DataFrame,
    views: PriceViews | None = None,
    publisher_fee: float = PUBLISHER_FEE_RATE,
) -> Metrics | None:
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
        change_24h=_change(recent, last_ts, timedelta(hours=24)),
        change_7d=_change(recent, last_ts, timedelta(days=7)),
        change_30d=_change(daily["price"], daily.index[-1], timedelta(days=30)),
        volatility_30d=float(returns.std()) if len(returns) > 1 else None,
        avg_daily_volume_30d=float(month["volume"].mean()) if not month.empty else None,
        proceeds_after_fee=seller_proceeds(round(last_price * 100), publisher_fee) / 100,
    )


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def recent_trend(frame: pd.DataFrame, days: int = 7, bucket: str = "6h") -> list[float]:
    """Prices of the last few days, averaged per bucket, for a small trend line."""
    if frame.empty:
        return []
    hourly = hourly_series(frame)["price"]
    series = hourly if not hourly.empty else daily_series(frame)["price"]
    recent = series[series.index >= series.index[-1] - timedelta(days=days)]
    return [float(v) for v in recent.resample(bucket).mean().dropna()]


def change_over(prices: pd.Series, period: timedelta | None) -> float | None:
    """Relative change of PriceViews.prices over the period; None = all history.

    When the history is shorter than the period, the change counts from its first point.
    """
    if prices.empty:
        return None
    if period is not None:
        before = prices[: prices.index[-1] - period]
        if not before.empty:
            prices = prices[before.index[-1] :]
    first = prices.iloc[0]
    return float(prices.iloc[-1] / first - 1) if first else None
