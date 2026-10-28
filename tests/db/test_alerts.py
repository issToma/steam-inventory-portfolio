from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from invinv import alerts
from invinv.core.parsers import HistoryPoint, PriceHistory, SearchResult
from invinv.core.telegram import TelegramError
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.db.tables import AlertEvent, FetchJob

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
NAME = "Kilowatt Case"
FOUND = SearchResult(NAME, "Base Grade Container", "G1", None, None, None, None, appid=730)


async def add_rule(session: AsyncSession, kind: str = "above", threshold: str = "2") -> int:
    rule = await repo.add_alert(session, NAME, kind, Decimal(threshold), found=FOUND, appid=730)
    await session.commit()
    return rule.id


async def set_price(session: AsyncSession, price: str, at: datetime = NOW) -> int:
    item = await repo.get_item_by_name(session, NAME, 730)
    assert item is not None
    history = PriceHistory(1, [HistoryPoint(at, "hour", Decimal(price), 10)])
    await repo.save_history(session, item.id, history)
    return item.id


async def jobs(session: AsyncSession) -> set[tuple[str, str]]:
    return {(j.kind, j.target) for j in await session.scalars(select(FetchJob))}


async def tracked(session: AsyncSession) -> bool:
    item = await repo.get_item_by_name(session, NAME, 730)
    return item is not None and await repo.is_tracked(session, item.id)


async def test_an_alert_keeps_its_item_collected(session: AsyncSession) -> None:
    rule_id = await add_rule(session)
    assert ("group", "730:G1") in await jobs(session)
    assert await tracked(session)

    await repo.delete_alert(session, rule_id)

    # The group job stops by itself once no item of the group is tracked.
    assert not await tracked(session)


async def test_a_disabled_alert_does_not_keep_collecting(session: AsyncSession) -> None:
    rule_id = await add_rule(session)

    await repo.set_alert_enabled(session, rule_id, False)
    assert not await tracked(session)
    await repo.set_alert_enabled(session, rule_id, True)
    assert await tracked(session)


async def test_alert_fires_once_and_lands_in_the_feed(session: AsyncSession) -> None:
    await add_rule(session, "above", "2")
    item_id = await set_price(session, "1.50")
    assert await alerts.check_items(session, [item_id], NOW) == 0

    await set_price(session, "2.10", NOW + timedelta(hours=1))
    assert await alerts.check_items(session, [item_id], NOW + timedelta(hours=1)) == 1
    await set_price(session, "2.20", NOW + timedelta(hours=2))
    assert await alerts.check_items(session, [item_id], NOW + timedelta(hours=2)) == 0

    feed = await repo.alert_events(session)
    assert [(e.kind, e.price_usd, e.telegram) for e, _ in feed] == [
        ("above", Decimal("2.1000"), repo.TELEGRAM_OFF)
    ]
    assert await repo.unread_alert_count(session) == 1
    await repo.mark_alerts_read(session)
    assert await repo.unread_alert_count(session) == 0


async def test_deleting_a_rule_keeps_its_notifications(session: AsyncSession) -> None:
    rule_id = await add_rule(session, "below", "5")
    item_id = await set_price(session, "1")
    await alerts.check_items(session, [item_id], NOW)

    await repo.delete_alert(session, rule_id)

    assert [e.rule_id for e, _ in await repo.alert_events(session)] == [None]


class FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    async def send(self, token: str, chat_id: str, html: str) -> None:
        if self.fail:
            raise TelegramError("Bad Gateway")
        self.sent.append((chat_id, html))


async def fire_with_telegram(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.set_setting(session, repo.SETTING_TELEGRAM_TOKEN, "1:x")
        await repo.set_setting(session, repo.SETTING_TELEGRAM_CHAT, "42")
        await add_rule(session, "above", "2")
        item_id = await set_price(session, "3")
        await alerts.check_items(session, [item_id], NOW)
        await session.commit()


async def telegram_states(sessions: SessionMaker) -> list[str]:
    async with sessions() as session:
        return [e.telegram for e in await session.scalars(select(AlertEvent))]


async def test_notifier_sends_pending_alerts_once(sessions: SessionMaker) -> None:
    await fire_with_telegram(sessions)
    bot = FakeBot()
    notifier = alerts.Notifier(sessions, bot)  # type: ignore[arg-type]

    assert await notifier.deliver(NOW) == 1
    assert await notifier.deliver(NOW) == 0

    assert bot.sent == [
        ("42", f"🔔 <b>{NAME}</b>\nCounter-Strike 2\nPrice at or above $2.00: now $3.00")
    ]
    assert await telegram_states(sessions) == [repo.TELEGRAM_SENT]


async def test_notifier_retries_then_gives_up(sessions: SessionMaker) -> None:
    await fire_with_telegram(sessions)
    async with sessions() as session:
        (event,) = await session.scalars(select(AlertEvent))
        created = event.created_at
    bot = FakeBot(fail=True)
    notifier = alerts.Notifier(sessions, bot)  # type: ignore[arg-type]

    await notifier.deliver(created)
    assert await telegram_states(sessions) == [repo.TELEGRAM_PENDING]
    # Within the retry pause nothing is attempted.
    assert await notifier.deliver(created + timedelta(minutes=1)) == 0

    await notifier.deliver(created + alerts.TELEGRAM_GIVE_UP)
    assert await telegram_states(sessions) == [repo.TELEGRAM_FAILED]
