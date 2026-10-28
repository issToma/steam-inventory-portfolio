from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from cs2market import alerts
from cs2market.alerts import Reading, is_met, step
from cs2market.db.tables import AlertEvent, AlertRule, Item

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
READING = Reading(price=15.0, change_24h=0.12, change_7d=-0.2)


@pytest.mark.parametrize(
    ("kind", "threshold", "window", "expected"),
    [
        ("above", 15, None, True),
        ("above", 15.01, None, False),
        ("below", 15, None, True),
        ("below", 14.99, None, False),
        ("rise", 10, 24, True),
        ("rise", 15, 24, False),
        ("fall", 20, 168, True),
        ("fall", 10, 24, False),
    ],
)
def test_is_met(kind: str, threshold: float, window: int | None, expected: bool) -> None:
    assert is_met(kind, threshold, window, READING) is expected


def test_change_rules_wait_for_enough_history() -> None:
    assert not is_met("rise", 1, 24, Reading(price=1.0, change_24h=None, change_7d=None))


def rule() -> AlertRule:
    return AlertRule(kind="above", threshold=Decimal(10), armed=True, last_fired_at=None)


def test_rule_fires_once_while_the_condition_holds() -> None:
    r = rule()

    assert step(r, True, NOW)
    assert not step(r, True, NOW + timedelta(hours=1))
    assert not step(r, True, NOW + timedelta(days=2))


def test_rule_fires_again_after_the_condition_stopped_holding() -> None:
    r = rule()
    step(r, True, NOW)

    assert not step(r, False, NOW + timedelta(hours=7))
    assert step(r, True, NOW + timedelta(hours=8))


def test_cooldown_keeps_a_hovering_price_quiet() -> None:
    r = rule()
    step(r, True, NOW)
    step(r, False, NOW + timedelta(hours=1))

    assert not step(r, True, NOW + timedelta(hours=2))
    # Still armed: it fires once the cool-down has passed, if the condition still holds.
    assert step(r, True, NOW + alerts.COOLDOWN)


def test_texts() -> None:
    assert alerts.describe("en", "above", Decimal("15"), None) == "Price at or above $15.00"
    assert alerts.describe("en", "rise", Decimal("10.0000"), 24) == "Rises by 10% within 24 hours"
    assert alerts.describe("ru", "fall", Decimal("7.5"), 168) == "Падение на 7.5% за 7 дней"
    event = AlertEvent(price_usd=Decimal("16.2"), change=0.125, window_hours=24)
    assert alerts.event_summary("en", event) == "now $16.20, +12.5% in 24 hours"


def test_telegram_message_escapes_html() -> None:
    event = AlertEvent(kind="below", threshold=Decimal(1), price_usd=Decimal("0.9"), change=None)
    item = Item(market_hash_name="Sticker | <b>Team</b> & Co")

    message = alerts.telegram_message("en", event, item, "Counter-Strike 2")

    assert "&lt;b&gt;Team&lt;/b&gt; &amp; Co" in message
    assert message.endswith("Price at or below $1.00: now $0.90")
