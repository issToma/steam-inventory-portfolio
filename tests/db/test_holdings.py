from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cs2market.core.parsers import InventoryItem
from cs2market.db import repo
from cs2market.db.tables import FetchJob, Holding


def inv(name: str, *, amount: int = 1, marketable: bool = True, group: str = "G1") -> InventoryItem:
    return InventoryItem(name, "Base Grade Container", group, "1", None, marketable, amount)


async def active(session: AsyncSession) -> dict[str, tuple[str, int]]:
    return {
        item.market_hash_name: (holding.source, holding.quantity)
        for holding, item in await repo.active_holdings(session)
    }


async def jobs(session: AsyncSession) -> set[tuple[str, str]]:
    return {(j.kind, j.target) for j in await session.scalars(select(FetchJob))}


async def test_inventory_sync_aggregates_and_skips_unsellable(session: AsyncSession) -> None:
    count = await repo.sync_inventory(
        session,
        [inv("Kilowatt Case"), inv("Kilowatt Case"), inv("Veteran Coin", marketable=False)],
    )

    assert count == 1
    assert await active(session) == {"Kilowatt Case": ("inventory", 2)}
    assert ("group", "730:G1") in await jobs(session)


async def test_item_leaving_and_returning_keeps_purchase_price(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case")])
    ((holding, _),) = await repo.active_holdings(session)
    await repo.update_holding(session, holding.id, buy_price_usd=Decimal("1.50"))

    await repo.sync_inventory(session, [])
    assert await active(session) == {}

    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=3)])
    ((holding, _),) = await repo.active_holdings(session)
    assert holding.quantity == 3
    assert holding.buy_price_usd == Decimal("1.50")


async def test_sync_leaves_manual_positions_alone(session: AsyncSession) -> None:
    await repo.add_manual_holding(session, "Kilowatt Case", quantity=50, location="Storage #1")
    await repo.sync_inventory(session, [inv("Kilowatt Case")])

    holdings = list(await session.scalars(select(Holding).order_by(Holding.source)))
    assert [(h.source, h.quantity, h.location) for h in holdings] == [
        ("inventory", 1, None),
        ("manual", 50, "Storage #1"),
    ]


async def test_inventory_quantity_is_not_editable(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case")])
    ((holding, _),) = await repo.active_holdings(session)

    await repo.update_holding(session, holding.id, quantity=99, location="nope")

    assert (holding.quantity, holding.location) == (1, None)


async def test_deleting_last_position_stops_collection(session: AsyncSession) -> None:
    holding = await repo.add_manual_holding(session, "Some Sticker", quantity=1)
    assert ("history", "730:Some Sticker") in await jobs(session)

    await repo.delete_manual_holding(session, holding.id)

    assert await active(session) == {}
    assert ("history", "730:Some Sticker") not in await jobs(session)


async def test_watchlist_removal_keeps_collecting_held_items(session: AsyncSession) -> None:
    item = await repo.add_to_watchlist(session, "Some Sticker")
    await repo.add_manual_holding(session, "Some Sticker", quantity=1)

    await repo.remove_from_watchlist(session, item.id)

    assert ("history", "730:Some Sticker") in await jobs(session)


async def test_settings(session: AsyncSession) -> None:
    assert await repo.get_setting(session, repo.SETTING_STEAM_ID) is None
    await repo.set_setting(session, repo.SETTING_STEAM_ID, "76561198000000042")
    await repo.set_setting(session, repo.SETTING_STEAM_ID, "76561198000000000")
    assert await repo.get_setting(session, repo.SETTING_STEAM_ID) == "76561198000000000"
    await repo.set_setting(session, repo.SETTING_STEAM_ID, None)
    assert await repo.get_setting(session, repo.SETTING_STEAM_ID) is None


async def test_large_inventory_sync(session: AsyncSession) -> None:
    # More rows than Postgres plans with custom plans before switching to a generic one.
    items = [inv(f"Case {n}", group=f"G{n}") for n in range(12)]

    assert await repo.sync_inventory(session, items) == 12
    assert await repo.sync_inventory(session, items) == 12
    assert len(await active(session)) == 12


async def test_pinned_position_survives_leaving_the_inventory(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=5), inv("Other Case")])
    await repo.pin_all(session, pinned=True)

    # Three cases go to a storage unit, the other case is sold... and stays pinned.
    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=2)])
    assert await active(session) == {
        "Kilowatt Case": ("inventory", 5),
        "Other Case": ("inventory", 1),
    }

    # Buying more raises the pinned quantity.
    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=7)])
    assert (await active(session))["Kilowatt Case"] == ("inventory", 7)


async def test_unpinned_position_follows_the_inventory_again(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=5)])
    ((holding, _),) = await repo.active_holdings(session)
    await repo.set_pinned(session, holding.id, True)
    await repo.set_pinned(session, holding.id, False)

    await repo.sync_inventory(session, [inv("Kilowatt Case", amount=2)])

    assert await active(session) == {"Kilowatt Case": ("inventory", 2)}


def rust(name: str) -> InventoryItem:
    return InventoryItem(name, "Hoodie", None, "9", None, True, 1, appid=252490)


async def test_same_name_in_two_games_is_two_items(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Wooden Box"), rust("Wooden Box")])

    holdings = await repo.active_holdings(session)

    assert sorted(item.appid for _, item in holdings) == [730, 252490]


async def test_game_that_failed_to_load_keeps_its_positions(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case"), rust("Tempered Hoodie")])

    await repo.sync_inventory(session, [inv("Kilowatt Case")], untouched=frozenset({252490}))
    assert set(await active(session)) == {"Kilowatt Case", "Tempered Hoodie"}

    await repo.sync_inventory(session, [inv("Kilowatt Case")])
    assert set(await active(session)) == {"Kilowatt Case"}
