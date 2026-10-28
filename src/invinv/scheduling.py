"""How often to refresh an item's current price, and in which order to fetch (SPEC F17).

Requests are scarce, so a big inventory cannot refresh everything every hour. Items the user
watches, valuable positions and anything we cannot value yet refresh at the base interval;
cheap positions and rarely traded items refresh less often.
"""

from dataclasses import dataclass
from datetime import timedelta

SIX_HOURS = timedelta(hours=6)
DAY = timedelta(days=1)

# Value of the user's positions in a group, in USD.
VALUABLE_USD = 20.0
MODEST_USD = 2.0
# Fewer sales per day than this and the price hardly moves between refreshes.
ILLIQUID_SALES_PER_DAY = 1.0


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


# Order of the first price downloads, highest first: a big inventory takes hours to load, so
# items the user watches and the likely valuable ones (by the rarity word in their market
# type, e.g. "★ Covert Knife", "Immortal Wearable") come before the cheap bulk.
WATCHED_PRIORITY = 10
# Listing jobs run ahead of history jobs: an item is valued once its listing is known.
LISTING_PRIORITY_BONUS = 20
UNKNOWN_PRIORITY = 4
_RARITY_PRIORITY = {
    "★": 9,
    "Contraband": 9,
    "Arcana": 9,
    "Covert": 8,
    "Immortal": 8,
    "Extraordinary": 8,
    "Classified": 7,
    "Legendary": 7,
    "Exotic": 7,
    "Restricted": 6,
    "Mythical": 6,
    "Remarkable": 6,
    "Mil-Spec": 5,
    "Rare": 5,
    "High": 5,
    "Foil": 5,
    "Industrial": 4,
    "Uncommon": 4,
    "Consumer": 3,
    "Base": 3,
    "Common": 3,
}


def fetch_priority(item_type: str | None, *, watched: bool) -> int:
    """How soon to fetch an item's prices among the due jobs (higher first)."""
    if watched:
        return WATCHED_PRIORITY
    for word in (item_type or "").split():
        if word in _RARITY_PRIORITY:
            return _RARITY_PRIORITY[word]
    return UNKNOWN_PRIORITY
