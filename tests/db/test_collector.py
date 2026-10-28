from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from cs2market.collector import Collector
from cs2market.core.parsers import (
    GroupVariant,
    HistoryPoint,
    InventoryItem,
    PriceHistory,
    SearchResult,
)
from cs2market.core.steam import InventoryPrivateError, SteamMarket
from cs2market.db import repo
from cs2market.db.session import SessionMaker
from cs2market.settings import Settings

STEAM_ID = "76561198000000000"
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


class FakeMarket:
    def __init__(self, inventory: list[InventoryItem] | Exception) -> None:
        self._inventory = inventory
        self.calls: list[str] = []

    async def inventory(self, steam_id64: str) -> list[InventoryItem]:
        self.calls.append(steam_id64)
        if isinstance(self._inventory, Exception):
            raise self._inventory
        return self._inventory


def collector(sessions: SessionMaker, market: Any) -> Collector:
    return Collector(sessions, cast(SteamMarket, market), Settings(), now=lambda: NOW)


async def run_inventory_job(sessions: SessionMaker, market: FakeMarket) -> None:
    async with sessions() as session:
        await repo.set_inventory_owner(session, STEAM_ID)
        await session.commit()
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert job is not None
    await collector(sessions, market)._run(job)


async def test_inventory_job_imports_sellable_items(sessions: SessionMaker) -> None:
    case = InventoryItem("Kilowatt Case", "Base Grade Container", "G1", "1", None, True, 1)
    coin = InventoryItem("Veteran Coin", "Extraordinary Collectible", None, "2", None, False, 1)
    market = FakeMarket([case, case, coin])

    await run_inventory_job(sessions, market)

    assert market.calls == [STEAM_ID]
    async with sessions() as session:
        held = [(i.market_hash_name, h.quantity) for h, i in await repo.active_holdings(session)]
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert held == [("Kilowatt Case", 2)]
    assert job is not None
    assert job.status == "ok"
    assert job.run_after == NOW.replace(hour=18)


async def test_private_inventory_is_reported_and_retried(sessions: SessionMaker) -> None:
    await run_inventory_job(sessions, FakeMarket(InventoryPrivateError(STEAM_ID)))

    async with sessions() as session:
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert job is not None
    assert job.status == "error"
    assert "not public" in (job.last_error or "")
    assert job.run_after == NOW.replace(hour=13)


async def test_switching_accounts_drops_the_old_job(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.set_inventory_owner(session, STEAM_ID)
        await repo.set_inventory_owner(session, "76561198000000001")
        await session.commit()
        assert await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID) is None
        assert await repo.get_job(session, repo.JOB_INVENTORY, "76561198000000001") is not None


class HistoryMarket:
    """Serves price history either per item or as a group page."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @staticmethod
    def _history() -> PriceHistory:
        return PriceHistory(1, [HistoryPoint(NOW, "hour", Decimal("50"), 30)])

    async def price_history(self, name: str) -> PriceHistory:
        self.calls.append(f"item:{name}")
        return self._history()

    async def group_page(self, group: str) -> dict[str, GroupVariant]:
        self.calls.append(f"group:{group}")
        names = ["AK-47 | Redline (Field-Tested)", "AK-47 | Redline (Minimal Wear)"]
        return {
            n: GroupVariant(
                n, "Classified Rifle", None, None, "The Phoenix Collection", self._history()
            )
            for n in names
        }


async def run_group_job(sessions: SessionMaker, market: HistoryMarket, names: list[str]) -> None:
    async with sessions() as session:
        for name in names:
            found = SearchResult(name, "Classified Rifle", "G1", None, None, None, None)
            await repo.add_manual_holding(session, name, quantity=1, found=found)
        await session.commit()
        job = await repo.get_job(session, repo.JOB_GROUP, "G1")
    assert job is not None
    await collector(sessions, market)._run(job)


async def test_single_variant_group_uses_the_light_endpoint(sessions: SessionMaker) -> None:
    market = HistoryMarket()

    await run_group_job(sessions, market, ["AK-47 | Redline (Field-Tested)"])

    assert market.calls == ["item:AK-47 | Redline (Field-Tested)"]
    async with sessions() as session:
        job = await repo.get_job(session, repo.JOB_GROUP, "G1")
    assert job is not None
    # $50 held, 1 sale a day: valuable and liquid enough for the hourly refresh.
    assert job.run_after == NOW.replace(hour=13)


async def test_several_variants_share_one_group_page(sessions: SessionMaker) -> None:
    market = HistoryMarket()

    await run_group_job(
        sessions, market, ["AK-47 | Redline (Field-Tested)", "AK-47 | Redline (Minimal Wear)"]
    )

    assert market.calls == ["group:G1"]
