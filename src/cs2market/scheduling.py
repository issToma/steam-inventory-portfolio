"""How often to refresh a group's price history (docs/SPEC.md, F17).

Requests are scarce, so a big inventory cannot refresh everything every hour. Items the user
watches, valuable positions and anything we cannot value yet refresh at the base interval;
cheap positions and rarely traded items refresh less often.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from cs2market.core.currency import USD
from cs2market.core.parsers import PriceHistory

SIX_HOURS = timedelta(hours=6)
DAY = timedelta(days=1)

# Value of the user's positions in a group, in USD.
VALUABLE_USD = 20.0
MODEST_USD = 2.0
# Fewer sales per day than this and the price hardly moves between refreshes.
ILLIQUID_SALES_PER_DAY = 1.0
SALES_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class Importance:
    watched: bool
    held_value_usd: float | None  # None when the value is unknown (no price or rate yet)
    sales_per_day: float


def refresh_interval(importance: Importance, base: timedelta) -> timedelta:
    if importance.watched or importance.held_value_usd is None:
        return base
    if importance.held_value_usd >= VALUABLE_USD:
        interval = base
    elif importance.held_value_usd >= MODEST_USD:
        interval = SIX_HOURS
    else:
        interval = DAY
    if importance.sales_per_day < ILLIQUID_SALES_PER_DAY:
        interval = max(interval, SIX_HOURS)
    return max(interval, base)


def sales_per_day(history: PriceHistory, now: datetime) -> float:
    """Average sales per day over the last 30 days (Steam keeps those hourly)."""
    since = now - SALES_WINDOW
    recent = [p for p in history.points if p.ts >= since]
    return sum(p.volume for p in recent) / SALES_WINDOW.days


def held_value_usd(
    holdings: Iterable[tuple[int, PriceHistory]], fx: Mapping[int, Decimal]
) -> float | None:
    """Value of (quantity, history) pairs at their latest price, or None if unknown."""
    total = 0.0
    for quantity, history in holdings:
        if not history.points:
            continue
        if history.currency == USD:
            rate = Decimal(1)
        elif history.currency in fx:
            rate = fx[history.currency]
        else:
            return None
        total += float(history.points[-1].price * rate) * quantity
    return total
