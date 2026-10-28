from datetime import UTC, datetime
from typing import Any, cast

from cs2market.collector import Collector
from cs2market.core.parsers import InventoryItem
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
