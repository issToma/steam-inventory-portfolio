from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from invinv.collector import Collector
from invinv.core.http import RATE_LIMITED_MESSAGE
from invinv.core.parsers import (
    GroupVariant,
    HistoryPoint,
    InventoryApp,
    InventoryItem,
    PriceHistory,
    ProfileRef,
    SearchResult,
    SteamProfile,
)
from invinv.core.steam import InventoryPrivateError, SteamMarket
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.settings import Settings

STEAM_ID = "76561198000000000"
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


class FakeMarket:
    def __init__(self, inventory: list[InventoryItem] | Exception, privacy: str = "public") -> None:
        self._inventory = inventory
        self._privacy = privacy
        self.calls: list[str] = []

    async def profile(self, ref: ProfileRef) -> SteamProfile:
        self.calls.append(f"profile:{ref.steam_id64}")
        assert ref.steam_id64 is not None
        return SteamProfile(ref.steam_id64, "Tester", None, "https://example", self._privacy)

    async def inventory_apps(self, steam_id64: str) -> list[InventoryApp]:
        if isinstance(self._inventory, Exception):
            raise self._inventory
        return [InventoryApp(730, "Counter-Strike 2", None, {"2": len(self._inventory)})]

    async def inventory(
        self, steam_id64: str, appid: int = 730, contextid: str = "2"
    ) -> list[InventoryItem]:
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
    case = InventoryItem(
        "Kilowatt Case", "Base Grade Container", "G1", "1", None, True, 1, appid=730
    )
    coin = InventoryItem(
        "Veteran Coin", "Extraordinary Collectible", None, "2", None, False, 1, appid=730
    )
    market = FakeMarket([case, case, coin])

    await run_inventory_job(sessions, market)

    assert market.calls == [f"profile:{STEAM_ID}", STEAM_ID]
    async with sessions() as session:
        held = [(i.market_hash_name, h.quantity) for h, i in await repo.active_holdings(session)]
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert held == [("Kilowatt Case", 2)]
    async with sessions() as session:
        account = await repo.get_account(session)
    assert account is not None
    assert account.profile is not None
    assert account.profile.name == "Tester"
    assert account.positions == 1
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

    async def price_history(self, name: str, appid: int = 730) -> PriceHistory:
        self.calls.append(f"item:{name}")
        return self._history()

    async def group_page(self, group: str, appid: int = 730) -> dict[str, GroupVariant]:
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
            found = SearchResult(name, "Classified Rifle", "G1", None, None, None, None, appid=730)
            await repo.add_manual_holding(session, name, quantity=1, found=found, appid=730)
        await session.commit()
        job = await repo.get_job(session, repo.JOB_GROUP, "730:G1")
    assert job is not None
    await collector(sessions, market)._run(job)


async def test_single_variant_group_uses_the_light_endpoint(sessions: SessionMaker) -> None:
    market = HistoryMarket()

    await run_group_job(sessions, market, ["AK-47 | Redline (Field-Tested)"])

    assert market.calls == ["item:AK-47 | Redline (Field-Tested)"]
    async with sessions() as session:
        job = await repo.get_job(session, repo.JOB_GROUP, "730:G1")
    assert job is not None
    # $50 held, 1 sale a day: valuable and liquid enough for the hourly refresh.
    assert job.run_after == NOW.replace(hour=13)


async def test_several_variants_share_one_group_page(sessions: SessionMaker) -> None:
    market = HistoryMarket()

    await run_group_job(
        sessions, market, ["AK-47 | Redline (Field-Tested)", "AK-47 | Redline (Minimal Wear)"]
    )

    assert market.calls == ["group:G1"]


async def test_private_profile_skips_the_inventory_request(sessions: SessionMaker) -> None:
    market = FakeMarket([], privacy="private")

    await run_inventory_job(sessions, market)

    assert market.calls == [f"profile:{STEAM_ID}"]
    async with sessions() as session:
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert job is not None
    assert "not public" in (job.last_error or "")


async def test_fresh_public_profile_is_not_fetched_again(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.set_inventory_owner(session, STEAM_ID)
        await repo.save_profile(
            session, SteamProfile(STEAM_ID, "Tester", None, "https://example", "public"), NOW
        )
        await session.commit()
        job = await repo.get_job(session, repo.JOB_INVENTORY, STEAM_ID)
    assert job is not None
    case = InventoryItem(
        "Kilowatt Case", "Base Grade Container", "G1", "1", None, True, 1, appid=730
    )
    market = FakeMarket([case])

    await collector(sessions, market)._run(job)

    assert market.calls == [STEAM_ID]


class TwoGamesMarket(FakeMarket):
    async def inventory_apps(self, steam_id64: str) -> list[InventoryApp]:
        return [
            InventoryApp(730, "Counter-Strike 2", None, {"2": 1}),
            InventoryApp(252490, "Rust", None, {"2": 1}),
        ]

    async def inventory(
        self, steam_id64: str, appid: int = 730, contextid: str = "2"
    ) -> list[InventoryItem]:
        self.calls.append(f"{appid}/{contextid}")
        if appid == 730:
            return [
                InventoryItem(
                    "Kilowatt Case", "Base Grade Container", "G1", "1", None, True, 1, appid=730
                )
            ]
        return [InventoryItem("Tempered Hoodie", "Hoodie", None, "9", None, True, 1, appid=252490)]


async def test_inventory_of_every_game_is_imported(sessions: SessionMaker) -> None:
    market = TwoGamesMarket([])

    await run_inventory_job(sessions, market)

    assert market.calls[1:] == ["730/2", "252490/2"]
    async with sessions() as session:
        held = sorted((i.appid, i.market_hash_name) for _, i in await repo.active_holdings(session))
        games = [g.name for g in await repo.inventory_games(session)]
    assert held == [(730, "Kilowatt Case"), (252490, "Tempered Hoodie")]
    assert games == ["Counter-Strike 2", "Rust"]


async def test_switched_off_game_is_not_imported(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.set_game_enabled(session, 252490, False)
        await session.commit()
    market = TwoGamesMarket([])

    await run_inventory_job(sessions, market)

    assert "252490/2" not in market.calls
    async with sessions() as session:
        held = [i.appid for _, i in await repo.active_holdings(session)]
    assert held == [730]


async def test_resuming_runs_rate_limited_jobs_now(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.ensure_job(session, repo.JOB_FX, "5")
        await session.commit()
        job = await repo.get_job(session, repo.JOB_FX, "5")
        assert job is not None
        later = datetime.now(UTC) + timedelta(minutes=10)
        await repo.fail_job(session, job, RATE_LIMITED_MESSAGE, later, count_attempt=False)
        await session.commit()
        assert await repo.due_job_count(session) == 0

        await repo.retry_rate_limited_jobs(session)
        await session.commit()

        assert await repo.due_job_count(session) == 1
