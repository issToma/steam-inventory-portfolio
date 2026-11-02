"""An item's current price: the cheapest listing on the market right now.

Steam's own inventory shows the same "Starting at" price. It comes from priceoverview, a
separate request per item, refreshed by the item's importance. When the item has no
listings, or they were not checked for a while, the price falls back to the median of its
last RECENT_SALES sales: for a liquid item that is the last hour, for a rare one the last
weeks, so a single odd trade (say, a $0.03 item bought for $30) cannot set the price.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import numpy as np
import pandas as pd

from invinv.analysis import PriceViews
from invinv.core.fees import PUBLISHER_FEE_RATE, seller_proceeds
from invinv.db.tables import Item

# Older listing data is not trusted as the current price.
LISTING_MAX_AGE = timedelta(days=3)
RECENT_SALES = 20

type PriceSource = Literal["listing", "sales"]


@dataclass(frozen=True)
class Listing:
    """What priceoverview said about an item, in USD."""

    lowest_usd: Decimal | None  # None when nothing is listed
    median_24h_usd: Decimal | None
    sales_24h: int | None
    checked_at: datetime


@dataclass(frozen=True)
class Quote:
    price: float
    source: PriceSource


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    return float(values[np.searchsorted(cumulative, cumulative[-1] / 2)])


def recent_sales_price(views: PriceViews, sales: int = RECENT_SALES) -> float | None:
    """Median price of the last `sales` sales (whole points, weighted by their sales)."""
    hourly = views.hourly
    daily = views.daily
    if not hourly.empty:
        daily = daily[daily.index < hourly.index[0]]
    points = pd.concat([daily, hourly])
    points = points[points["volume"] > 0]
    if points.empty:
        return None
    volumes = points["volume"].to_numpy()[::-1]
    # Newest first, as many points as it takes to cover `sales` sales.
    count = int(np.searchsorted(np.cumsum(volumes), sales)) + 1
    recent = points.iloc[::-1].iloc[:count]
    return weighted_median(recent["price"].to_numpy(), recent["volume"].to_numpy())


def quote(listing: Listing | None, sales_price: float | None, now: datetime) -> Quote | None:
    """The current price: the cheapest listing if fresh, else recent_sales_price()."""
    if (
        listing is not None
        and listing.lowest_usd is not None
        and now - listing.checked_at <= LISTING_MAX_AGE
    ):
        return Quote(float(listing.lowest_usd), "listing")
    return Quote(sales_price, "sales") if sales_price is not None else None


def listing_of(item: Item) -> Listing | None:
    if item.listing_checked_at is None:
        return None
    return Listing(
        lowest_usd=item.lowest_listing_usd,
        median_24h_usd=item.median_24h_usd,
        sales_24h=item.sales_24h,
        checked_at=item.listing_checked_at,
    )


@dataclass(frozen=True)
class InstantSale:
    """Selling at once into the buy orders, best first."""

    sold: int  # fewer than asked when the orders run out
    buyers_pay: Decimal
    you_receive: Decimal  # after Steam's and the game's fees, per unit


def instant_sale(
    bids: list[tuple[Decimal, int]], quantity: int, publisher_fee: float = PUBLISHER_FEE_RATE
) -> InstantSale:
    sold, paid, received = 0, Decimal(0), Decimal(0)
    for price, available in bids:
        take = min(available, quantity - sold)
        if take <= 0:
            break
        cents = round(price * 100)
        sold += take
        paid += price * take
        received += Decimal(seller_proceeds(cents, publisher_fee)) / 100 * take
    return InstantSale(sold=sold, buyers_pay=paid, you_receive=received)
