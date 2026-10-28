"""Price alerts (docs/SPEC.md, F11): rules checked after every price refresh.

A rule watches an item's latest price (with odd single sales filtered out, like the metrics)
or its change over 24 hours or 7 days. It fires when its condition starts to hold, then waits
until the condition stops holding before it can fire again; COOLDOWN keeps a price that
hovers around the threshold from notifying on every refresh.
"""

import html
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from invinv.analysis import MissingRateError, compute_metrics, price_views, to_frame
from invinv.core.games import game_name
from invinv.core.telegram import TelegramBot, TelegramError
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.db.tables import AlertEvent, AlertRule, Item
from invinv.i18n import LANGUAGES, Language, tr_in
from invinv.pricing import Listing, listing_of, quote, recent_sales_price

log = logging.getLogger(__name__)

KIND_ABOVE = "above"
KIND_BELOW = "below"
KIND_RISE = "rise"
KIND_FALL = "fall"
PRICE_KINDS = (KIND_ABOVE, KIND_BELOW)
CHANGE_KINDS = (KIND_RISE, KIND_FALL)
# Windows for rise and fall rules, in hours.
WINDOWS = (24, 168)

COOLDOWN = timedelta(hours=6)
# A Telegram message that still cannot be sent after this long is given up.
TELEGRAM_GIVE_UP = timedelta(days=1)
TELEGRAM_RETRY = timedelta(minutes=5)


@dataclass(frozen=True)
class Reading:
    """What the rules of one item look at, in USD."""

    price: float
    change_24h: float | None
    change_7d: float | None

    def change(self, window_hours: int | None) -> float | None:
        return self.change_7d if window_hours == 168 else self.change_24h


def is_met(kind: str, threshold: float, window_hours: int | None, reading: Reading) -> bool:
    """Whether a rule's condition holds; a change threshold is in percent."""
    if kind == KIND_ABOVE:
        return reading.price >= threshold
    if kind == KIND_BELOW:
        return reading.price <= threshold
    change = reading.change(window_hours)
    if change is None:
        return False
    if kind == KIND_RISE:
        return change >= threshold / 100
    if kind == KIND_FALL:
        return change <= -threshold / 100
    raise ValueError(f"unknown alert kind {kind!r}")


def step(rule: AlertRule, met: bool, now: datetime) -> bool:
    """Advance a rule's state for a new reading; True when it fires now."""
    if not met:
        rule.armed = True
        return False
    cooled = rule.last_fired_at is None or now - rule.last_fired_at >= COOLDOWN
    if rule.armed and cooled:
        rule.armed = False
        rule.last_fired_at = now
        return True
    return False


def reading_from(
    points: Sequence[repo.StoredPoint],
    fx: Mapping[int, Decimal],
    listing: Listing | None,
    now: datetime,
) -> Reading | None:
    """The item's current price (pricing.quote) and its changes from the sales history."""
    try:
        frame = to_frame(points, fx)
    except MissingRateError:
        frame = to_frame([], fx)
    views = price_views(frame) if not frame.empty else None
    metrics = compute_metrics(frame, views)
    current = quote(listing, recent_sales_price(views) if views else None, now)
    if current is None:
        return None
    return Reading(
        current.price,
        metrics.change_24h if metrics else None,
        metrics.change_7d if metrics else None,
    )


async def check_items(session: AsyncSession, item_ids: list[int], now: datetime) -> int:
    """Check the enabled rules of freshly refreshed items; returns how many fired."""
    rules = await repo.enabled_alerts_for(session, item_ids)
    if not rules:
        return 0
    fx = await repo.latest_fx(session)
    points = await repo.load_histories(session, list({r.item_id for r in rules}))
    readings = {}
    for item_id, rows in points.items():
        item = await repo.get_item(session, item_id)
        listing = listing_of(item) if item else None
        readings[item_id] = reading_from(rows, fx, listing, now)
    telegram = await repo.telegram_settings(session)
    fired = 0
    for rule in rules:
        reading = readings.get(rule.item_id)
        if reading is None:
            continue
        met = is_met(rule.kind, float(rule.threshold), rule.window_hours, reading)
        if step(rule, met, now):
            fired += 1
            await repo.add_alert_event(
                session,
                rule,
                reading.price,
                reading.change(rule.window_hours) if rule.kind in CHANGE_KINDS else None,
                telegram=repo.TELEGRAM_PENDING if telegram else repo.TELEGRAM_OFF,
            )
    if fired:
        log.info("%d alert(s) fired", fired)
    return fired


def _money(value: float) -> str:
    return f"${value:,.2f}"


def window_label(language: Language, window_hours: int | None) -> str:
    return tr_in(language, "7 days") if window_hours == 168 else tr_in(language, "24 hours")


def describe(language: Language, kind: str, threshold: Decimal, window_hours: int | None) -> str:
    """A rule's condition in words, e.g. "Price at or above $15.00"."""
    if kind == KIND_ABOVE:
        return tr_in(language, "Price at or above {price}", price=_money(float(threshold)))
    if kind == KIND_BELOW:
        return tr_in(language, "Price at or below {price}", price=_money(float(threshold)))
    pct = f"{threshold.normalize():f}"
    window = window_label(language, window_hours)
    if kind == KIND_RISE:
        return tr_in(language, "Rises by {pct}% within {window}", pct=pct, window=window)
    return tr_in(language, "Falls by {pct}% within {window}", pct=pct, window=window)


def event_summary(language: Language, event: AlertEvent) -> str:
    """What happened, e.g. "now $16.20" or "now $16.20, +12.5% in 24 hours"."""
    price = _money(float(event.price_usd))
    if event.change is None:
        return tr_in(language, "now {price}", price=price)
    return tr_in(
        language,
        "now {price}, {change} in {window}",
        price=price,
        change=f"{event.change:+.1%}",
        window=window_label(language, event.window_hours),
    )


def telegram_message(language: Language, event: AlertEvent, item: Item, game: str) -> str:
    rule = describe(language, event.kind, event.threshold, event.window_hours)
    lines = [
        f"🔔 <b>{html.escape(item.market_hash_name)}</b>",
        html.escape(game),
        f"{html.escape(rule)}: {html.escape(event_summary(language, event))}",
    ]
    return "\n".join(line for line in lines if line)


def as_language(value: str) -> Language:
    return value if value in LANGUAGES else "en"


class Notifier:
    """Sends fired alerts to Telegram, retrying for a while when it cannot."""

    def __init__(self, sessions: SessionMaker, bot: TelegramBot) -> None:
        self._sessions = sessions
        self._bot = bot
        self._retry_at: datetime | None = None

    async def deliver(self, now: datetime) -> int:
        """Send pending messages; returns how many were sent."""
        if self._retry_at is not None and now < self._retry_at:
            return 0
        async with self._sessions() as session:
            settings = await repo.telegram_settings(session)
            pending = await repo.pending_telegram_events(session)
            games = {g.appid: g.name for g in await repo.inventory_games(session)}
            if settings is None:
                # Turned off since the alert fired: nothing to send it to.
                for event, _ in pending:
                    await repo.set_telegram_status(session, event.id, repo.TELEGRAM_OFF)
                await session.commit()
                return 0
        sent = 0
        language = as_language(settings.language)
        for event, item in pending:
            try:
                game = games.get(item.appid) or game_name(item.appid)
                await self._bot.send(
                    settings.token, settings.chat_id, telegram_message(language, event, item, game)
                )
            except TelegramError as exc:
                log.warning("Telegram: %s", exc)
                self._retry_at = now + TELEGRAM_RETRY
                async with self._sessions() as session:
                    for stale, _ in pending:
                        if now - stale.created_at >= TELEGRAM_GIVE_UP:
                            await repo.set_telegram_status(session, stale.id, repo.TELEGRAM_FAILED)
                    await session.commit()
                return sent
            async with self._sessions() as session:
                await repo.set_telegram_status(session, event.id, repo.TELEGRAM_SENT)
                await session.commit()
            sent += 1
        self._retry_at = None
        return sent
