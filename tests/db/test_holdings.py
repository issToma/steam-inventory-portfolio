from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from invinv.core.parsers import HistoryPoint, InventoryItem, PriceHistory
from invinv.db import repo
from invinv.db.tables import FetchJob, Holding


def inv(name: str, *, amount: int = 1, marketable: bool = True, group: str = "G1") -> InventoryItem:
    return InventoryItem(
        name, "Base Grade Container", group, "1", None, marketable, amount, appid=730
    )


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
    await repo.add_manual_holding(
        session, "Kilowatt Case", quantity=50, location="Storage #1", appid=730
    )
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
    holding = await repo.add_manual_holding(session, "Some Sticker", quantity=1, appid=730)
    assert ("history", "730:Some Sticker") in await jobs(session)

    await repo.delete_manual_holding(session, holding.id)

    assert await active(session) == {}
    assert ("history", "730:Some Sticker") not in await jobs(session)


async def test_watchlist_removal_keeps_collecting_held_items(session: AsyncSession) -> None:
    item = await repo.add_to_watchlist(session, "Some Sticker", appid=730)
    await repo.add_manual_holding(session, "Some Sticker", quantity=1, appid=730)

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


async def test_game_fee_from_the_inventory_is_stored(session: AsyncSession) -> None:
    own_fee = InventoryItem(
        "Hoodie", "Hoodie", None, "9", None, True, 1, publisher_fee=0.05, appid=730
    )
    await repo.sync_inventory(session, [own_fee, inv("Kilowatt Case")])

    holdings = await repo.active_holdings(session)
    fees = {item.market_hash_name: item.publisher_fee for _, item in holdings}

    assert fees == {"Hoodie": Decimal("0.0500"), "Kilowatt Case": None}


MAIN = "76561198000000001"
TWIN = "76561198000000002"


async def test_switching_accounts_hides_and_restores_positions(session: AsyncSession) -> None:
    await repo.set_inventory_owner(session, MAIN)
    await repo.sync_inventory(session, [inv("Kilowatt Case"), inv("Revolution Case")])
    await repo.pin_all(session, True)
    await repo.add_manual_holding(
        session, "Recoil Case", quantity=3, location="Storage #1", appid=730
    )

    await repo.set_inventory_owner(session, TWIN)
    assert await active(session) == {}
    await repo.sync_inventory(session, [inv("Kilowatt Case")])
    assert await active(session) == {"Kilowatt Case": ("inventory", 1)}

    await repo.set_inventory_owner(session, MAIN)
    assert await active(session) == {
        "Kilowatt Case": ("inventory", 1),
        "Revolution Case": ("inventory", 1),
        "Recoil Case": ("manual", 3),
    }
    assert all(h.pinned for h, _ in await repo.active_holdings(session) if h.source == "inventory")


async def test_hidden_positions_stop_their_collection(session: AsyncSession) -> None:
    await repo.set_inventory_owner(session, MAIN)
    await repo.add_manual_holding(session, "Recoil Case", quantity=1, appid=730)
    item = await repo.get_item_by_name(session, "Recoil Case", 730)
    assert item is not None

    await repo.set_inventory_owner(session, TWIN)
    assert not await repo.is_tracked(session, item.id)
    assert ("resolve", "730:Recoil Case") not in await jobs(session)

    await repo.set_inventory_owner(session, MAIN)
    assert await repo.is_tracked(session, item.id)
    assert ("resolve", "730:Recoil Case") in await jobs(session)


async def test_positions_made_without_an_account_go_to_the_first_one(
    session: AsyncSession,
) -> None:
    await repo.add_manual_holding(session, "Recoil Case", quantity=1, appid=730)

    await repo.set_inventory_owner(session, MAIN)
    assert await active(session) == {"Recoil Case": ("manual", 1)}
    await repo.set_inventory_owner(session, None)
    assert await active(session) == {}
    await repo.set_inventory_owner(session, MAIN)
    assert await active(session) == {"Recoil Case": ("manual", 1)}


def typed(name: str, item_type: str) -> InventoryItem:
    return InventoryItem(name, item_type, None, "1", None, True, 1, appid=570)


async def test_valuable_and_watched_items_are_fetched_first(session: AsyncSession) -> None:
    await repo.sync_inventory(
        session,
        [
            typed("Cheap Hat", "Common Wearable"),
            typed("Fancy Hat", "Immortal Wearable"),
            typed("Odd Hat", "Rare Wearable"),
        ],
    )
    await repo.add_to_watchlist(session, "Odd Hat", appid=570)

    order = []
    now = datetime.now(UTC) + timedelta(minutes=1)
    while (job := await repo.next_due_job(session, now)) is not None:
        order.append(job.target)
        await repo.finish_job(session, job, None)

    assert order == ["570:Odd Hat", "570:Fancy Hat", "570:Cheap Hat"]


async def test_price_progress(session: AsyncSession) -> None:
    await repo.sync_inventory(session, [inv("Kilowatt Case"), inv("Revolution Case")])
    item = await repo.get_item_by_name(session, "Kilowatt Case", 730)
    assert item is not None
    history = PriceHistory(1, [HistoryPoint(datetime.now(UTC), "hour", Decimal("1"), 1)])
    await repo.save_history(session, item.id, history)

    assert await repo.price_progress(session) == repo.PriceProgress(priced=1, total=2)
