"""Database access. Functions take a session and leave committing to the caller."""

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from cs2market.core.parsers import (
    InventoryItem,
    ItemTraits,
    PriceHistory,
    Resolution,
    SearchResult,
    SteamProfile,
    parse_item_traits,
)
from cs2market.db.tables import (
    AppSetting,
    CatalogEntry,
    FetchJob,
    FxRate,
    Holding,
    Item,
    PricePoint,
    WatchlistEntry,
)

JOB_RESOLVE = "resolve"
JOB_HISTORY = "history"
# One market group page holds the history of every variant in the group (docs/SPEC.md, S6).
JOB_GROUP = "group"
JOB_INVENTORY = "inventory"
JOB_FX = "fx"

# word_similarity() threshold for typo matches; 0.35 lets "redlin akk" find the AK Redline.
_MIN_SIMILARITY = 0.35

# asyncpg allows 32767 bind parameters per statement; a price point uses 6.
_UPSERT_BATCH = 2000


@dataclass(frozen=True)
class StoredPoint:
    ts: datetime
    resolution: Resolution
    price: Decimal
    volume: int
    currency: int


async def get_item_by_name(session: AsyncSession, market_hash_name: str) -> Item | None:
    result = await session.scalars(select(Item).where(Item.market_hash_name == market_hash_name))
    return result.one_or_none()


async def get_item(session: AsyncSession, item_id: int) -> Item | None:
    return await session.get(Item, item_id)


async def _track(session: AsyncSession, market_hash_name: str, found: SearchResult | None) -> Item:
    """Get or create an item and queue the jobs that keep its price history fresh.

    Nothing here talks to Steam: the collector does. `found` is the search result the user
    picked; with it the item needs no resolve job.
    """
    await session.execute(
        insert(Item).values(market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name)
    assert item is not None
    if found is not None:
        apply_search_result(item, found, parse_item_traits(market_hash_name, found.item_type))
    if item.item_type is None:
        await ensure_job(session, JOB_RESOLVE, market_hash_name)
    await ensure_history_job(session, item)
    return item


async def add_to_watchlist(
    session: AsyncSession, market_hash_name: str, found: SearchResult | None = None
) -> Item:
    item = await _track(session, market_hash_name, found)
    await session.execute(insert(WatchlistEntry).values(item_id=item.id).on_conflict_do_nothing())
    return item


async def remove_from_watchlist(session: AsyncSession, item_id: int) -> None:
    item = await get_item(session, item_id)
    if item is None:
        return
    await session.execute(delete(WatchlistEntry).where(WatchlistEntry.item_id == item_id))
    if not await is_tracked(session, item_id):
        await _drop_item_jobs(session, item)


async def _drop_item_jobs(session: AsyncSession, item: Item) -> None:
    """Stop collecting an item nobody tracks; group jobs stop on their own when empty."""
    await session.execute(
        delete(FetchJob).where(
            FetchJob.kind.in_([JOB_RESOLVE, JOB_HISTORY]),
            FetchJob.target == item.market_hash_name,
        )
    )


async def ensure_history_job(session: AsyncSession, item: Item) -> None:
    """Queue history collection: per group when the group is known, per item otherwise."""
    if item.bucket_group_id:
        await ensure_job(session, JOB_GROUP, item.bucket_group_id)
        await delete_job(session, JOB_HISTORY, item.market_hash_name)
    else:
        await ensure_job(session, JOB_HISTORY, item.market_hash_name)


def _tracked_condition() -> ColumnElement[bool]:
    """An item is tracked when it is in the watchlist or in an active portfolio position."""
    in_watchlist = select(WatchlistEntry.item_id).where(WatchlistEntry.item_id == Item.id)
    held = select(Holding.id).where(Holding.item_id == Item.id, Holding.removed_at.is_(None))
    return or_(in_watchlist.exists(), held.exists())


async def tracked_items_in_group(session: AsyncSession, bucket_group_id: str) -> list[Item]:
    result = await session.scalars(
        select(Item).where(Item.bucket_group_id == bucket_group_id, _tracked_condition())
    )
    return list(result)


async def in_watchlist(session: AsyncSession, item_id: int) -> bool:
    return await session.get(WatchlistEntry, item_id) is not None


async def is_tracked(session: AsyncSession, item_id: int) -> bool:
    found = await session.scalar(select(Item.id).where(Item.id == item_id, _tracked_condition()))
    return found is not None


async def watchlist_items(session: AsyncSession) -> list[Item]:
    result = await session.scalars(
        select(Item).join(WatchlistEntry).order_by(WatchlistEntry.added_at)
    )
    return list(result)


def apply_search_result(item: Item, result: SearchResult, traits: ItemTraits) -> None:
    item.item_type = result.item_type
    item.bucket_group_id = result.bucket_group_id
    item.classid = result.classid
    item.icon_url = result.icon_url
    item.rarity = traits.rarity
    item.quality = traits.quality
    item.exterior = traits.exterior


async def save_history(session: AsyncSession, item_id: int, history: PriceHistory) -> None:
    rows = [
        {
            "item_id": item_id,
            "resolution": p.resolution,
            "ts": p.ts,
            "price": p.price,
            "volume": p.volume,
            "currency": history.currency,
        }
        for p in history.points
    ]
    for chunk in batched(rows, _UPSERT_BATCH, strict=False):
        stmt = insert(PricePoint).values(list(chunk))
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PricePoint.item_id, PricePoint.resolution, PricePoint.ts],
                set_={
                    "price": stmt.excluded.price,
                    "volume": stmt.excluded.volume,
                    "currency": stmt.excluded.currency,
                    "fetched_at": func.now(),
                },
            )
        )


async def load_history(
    session: AsyncSession, item_id: int, since: datetime | None = None
) -> list[StoredPoint]:
    query = (
        select(
            PricePoint.ts,
            PricePoint.resolution,
            PricePoint.price,
            PricePoint.volume,
            PricePoint.currency,
        )
        .where(PricePoint.item_id == item_id)
        .order_by(PricePoint.ts)
    )
    if since is not None:
        query = query.where(PricePoint.ts >= since)
    result = await session.execute(query)
    return [
        StoredPoint(ts, cast(Resolution, resolution), price, volume, currency)
        for ts, resolution, price, volume, currency in result
    ]


async def save_fx(session: AsyncSession, day: date, currency: int, usd_per_unit: Decimal) -> None:
    stmt = insert(FxRate).values(day=day, currency=currency, usd_per_unit=usd_per_unit)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[FxRate.day, FxRate.currency],
            set_={"usd_per_unit": stmt.excluded.usd_per_unit},
        )
    )


async def latest_fx(session: AsyncSession) -> dict[int, Decimal]:
    """The most recent Steam rate to USD for every currency we have seen."""
    result = await session.execute(
        select(FxRate.currency, FxRate.usd_per_unit)
        .ext(distinct_on(FxRate.currency))
        .order_by(FxRate.currency, FxRate.day.desc())
    )
    return {currency: rate for currency, rate in result}


async def ensure_job(
    session: AsyncSession, kind: str, target: str, run_after: datetime | None = None
) -> None:
    values: dict[str, object] = {"kind": kind, "target": target}
    if run_after is not None:
        values["run_after"] = run_after
    await session.execute(insert(FetchJob).values(**values).on_conflict_do_nothing())


async def next_due_job(session: AsyncSession, now: datetime) -> FetchJob | None:
    result = await session.scalars(
        select(FetchJob)
        .where(FetchJob.run_after <= now)
        .order_by(FetchJob.run_after, FetchJob.id)
        .limit(1)
    )
    return result.one_or_none()


async def next_run_time(session: AsyncSession) -> datetime | None:
    return await session.scalar(select(func.min(FetchJob.run_after)))


async def finish_job(session: AsyncSession, job: FetchJob, next_run: datetime | None) -> None:
    """Mark a job done: delete one-off jobs, reschedule recurring ones."""
    if next_run is None:
        await session.execute(delete(FetchJob).where(FetchJob.id == job.id))
        return
    await session.execute(
        update(FetchJob)
        .where(FetchJob.id == job.id)
        .values(status="ok", attempts=0, last_error=None, run_after=next_run)
    )


async def fail_job(
    session: AsyncSession,
    job: FetchJob,
    error: str,
    retry_at: datetime,
    *,
    count_attempt: bool = True,
) -> None:
    """Record a failure. Rate-limit pauses are not the job's fault and do not count."""
    await session.execute(
        update(FetchJob)
        .where(FetchJob.id == job.id)
        .values(
            status="error",
            attempts=FetchJob.attempts + (1 if count_attempt else 0),
            last_error=error,
            run_after=retry_at,
        )
    )


async def delete_job(session: AsyncSession, kind: str, target: str) -> None:
    await session.execute(delete(FetchJob).where(FetchJob.kind == kind, FetchJob.target == target))


async def jobs_for(session: AsyncSession, items: list[Item]) -> dict[int, FetchJob]:
    """Per item id, the job that best explains its status: a failing one if any."""
    by_target: dict[str, list[Item]] = {}
    for item in items:
        by_target.setdefault(item.market_hash_name, []).append(item)
        if item.bucket_group_id:
            by_target.setdefault(item.bucket_group_id, []).append(item)
    result = await session.scalars(
        select(FetchJob).where(
            FetchJob.kind.in_([JOB_RESOLVE, JOB_HISTORY, JOB_GROUP]),
            FetchJob.target.in_(list(by_target)),
        )
    )
    jobs: dict[int, FetchJob] = {}
    for job in result:
        for item in by_target[job.target]:
            current = jobs.get(item.id)
            if current is None or (job.status == "error" and current.status != "error"):
                jobs[item.id] = job
    return jobs


@dataclass(frozen=True)
class CatalogRecord:
    market_hash_name: str
    item_type: str | None
    bucket_group_id: str | None = None
    classid: str | None = None
    icon_url: str | None = None
    sell_price_usd_cents: int | None = None
    sell_listings: int | None = None


async def upsert_catalog(session: AsyncSession, records: list[CatalogRecord]) -> None:
    """Remember items; a record without a price keeps the price we already know."""
    for record in records:
        category = (
            parse_item_traits(record.market_hash_name, record.item_type).category
            if record.item_type
            else None
        )
        values: dict[str, object] = {
            "market_hash_name": record.market_hash_name,
            "item_type": record.item_type,
            "category": category,
            "bucket_group_id": record.bucket_group_id,
            "classid": record.classid,
            "icon_url": record.icon_url,
        }
        if record.sell_price_usd_cents is not None:
            values |= {
                "sell_price_usd_cents": record.sell_price_usd_cents,
                "sell_listings": record.sell_listings,
                "price_seen_at": func.now(),
            }
        stmt = insert(CatalogEntry).values(**values)
        update_set: dict[str, Any] = {
            key: func.coalesce(stmt.excluded[key], CatalogEntry.__table__.c[key])
            for key in values
            if key != "market_hash_name"
        }
        update_set["seen_at"] = func.now()
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CatalogEntry.market_hash_name], set_=update_set
            )
        )


async def search_catalog(
    session: AsyncSession,
    text: str,
    *,
    category: str | None = None,
    quality: str | None = None,
    exterior_name: str | None = None,
    limit: int = 20,
) -> list[CatalogEntry]:
    """Typo-tolerant search: names containing every word first, then trigram matches."""
    name = CatalogEntry.market_hash_name
    words = text.split()
    query = select(CatalogEntry)
    if words:
        contains_all = and_(*(name.icontains(w, autoescape=True) for w in words))
        similarity = func.word_similarity(text, name)
        query = query.where(or_(contains_all, similarity >= _MIN_SIMILARITY)).order_by(
            case((contains_all, 0), else_=1), similarity.desc()
        )
    if category is not None:
        query = query.where(CatalogEntry.category == category)
    if quality == "stattrak":
        query = query.where(name.contains("StatTrak™"))
    elif quality == "souvenir":
        query = query.where(name.startswith("Souvenir "))
    elif quality == "normal":
        query = query.where(~name.contains("StatTrak™"), ~name.startswith("Souvenir "))
    if exterior_name is not None:
        query = query.where(name.endswith(f"({exterior_name})"))
    query = query.order_by(CatalogEntry.sell_listings.desc().nulls_last()).limit(limit)
    return list(await session.scalars(query))


SOURCE_INVENTORY = "inventory"
SOURCE_MANUAL = "manual"

SETTING_STEAM_ID = "steam_id64"
# Profile details shown on the portfolio page, refreshed with the inventory.
SETTING_PROFILE = "steam_profile"
SETTING_INVENTORY_POSITIONS = "inventory_positions"


async def add_manual_holding(
    session: AsyncSession,
    market_hash_name: str,
    *,
    quantity: int,
    location: str | None = None,
    buy_price_usd: Decimal | None = None,
    bought_at: date | None = None,
    found: SearchResult | None = None,
) -> Holding:
    item = await _track(session, market_hash_name, found)
    holding = Holding(
        item_id=item.id,
        source=SOURCE_MANUAL,
        location=location or None,
        quantity=quantity,
        buy_price_usd=buy_price_usd,
        bought_at=bought_at,
    )
    session.add(holding)
    await session.flush()
    return holding


async def update_holding(
    session: AsyncSession,
    holding_id: int,
    *,
    quantity: int | None = None,
    location: str | None = None,
    buy_price_usd: Decimal | None = None,
    bought_at: date | None = None,
) -> None:
    """Edit a position. Quantity and location of inventory positions come from Steam."""
    holding = await session.get(Holding, holding_id)
    if holding is None:
        return
    if holding.source == SOURCE_MANUAL:
        if quantity is not None:
            holding.quantity = quantity
        holding.location = location or None
    holding.buy_price_usd = buy_price_usd
    holding.bought_at = bought_at


async def delete_manual_holding(session: AsyncSession, holding_id: int) -> None:
    holding = await session.get(Holding, holding_id)
    if holding is None or holding.source != SOURCE_MANUAL:
        return
    item = await get_item(session, holding.item_id)
    await session.delete(holding)
    await session.flush()
    if item is not None and not await is_tracked(session, item.id):
        await _drop_item_jobs(session, item)


async def active_holdings(session: AsyncSession) -> list[tuple[Holding, Item]]:
    result = await session.execute(
        select(Holding, Item)
        .join(Item, Holding.item_id == Item.id)
        .where(Holding.removed_at.is_(None))
        .order_by(Holding.source, Item.market_hash_name)
        # Re-read rows the session already holds: the inventory sync updates them with
        # bulk statements that bypass the identity map.
        .execution_options(populate_existing=True)
    )
    return [(holding, item) for holding, item in result]


async def sync_inventory(session: AsyncSession, items: list[InventoryItem]) -> int:
    """Make the inventory positions match the given inventory. Returns the position count.

    Items that cannot be sold on the market are ignored: they have no price.
    """
    quantities: dict[str, int] = {}
    details: dict[str, InventoryItem] = {}
    for entry in items:
        if entry.marketable:
            quantities[entry.market_hash_name] = (
                quantities.get(entry.market_hash_name, 0) + entry.amount
            )
            details[entry.market_hash_name] = entry

    await upsert_catalog(
        session,
        [
            CatalogRecord(
                market_hash_name=e.market_hash_name,
                item_type=e.item_type,
                bucket_group_id=e.bucket_group_id,
                classid=e.classid,
                icon_url=e.icon_url,
            )
            for e in details.values()
        ],
    )
    present: set[int] = set()
    for name, quantity in quantities.items():
        entry = details[name]
        found = SearchResult(
            market_hash_name=name,
            item_type=entry.item_type,
            bucket_group_id=entry.bucket_group_id,
            classid=entry.classid,
            icon_url=entry.icon_url,
            sell_listings=None,
            sell_price_usd_cents=None,
        )
        item = await _track(session, name, found)
        present.add(item.id)
        stmt = insert(Holding).values(
            item_id=item.id, source=SOURCE_INVENTORY, quantity=quantity, removed_at=None
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Holding.item_id],
                # A literal, not a bound parameter: once Postgres switches the prepared
                # statement to a generic plan it cannot match a parameter to the partial
                # unique index, and the insert fails from the sixth row on.
                index_where=text(f"source = '{SOURCE_INVENTORY}'"),
                set_={"quantity": stmt.excluded.quantity, "removed_at": None},
            )
        )

    gone = await session.scalars(
        select(Holding).where(
            Holding.source == SOURCE_INVENTORY,
            Holding.removed_at.is_(None),
            Holding.item_id.not_in(present),
        )
    )
    for holding in gone:
        holding.removed_at = func.now()
    return len(present)


async def get_setting(session: AsyncSession, key: str) -> str | None:
    setting = await session.get(AppSetting, key)
    return setting.value if setting else None


async def set_setting(session: AsyncSession, key: str, value: str | None) -> None:
    if value is None:
        await session.execute(delete(AppSetting).where(AppSetting.key == key))
        return
    stmt = insert(AppSetting).values(key=key, value=value)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": value})
    )


async def run_job_now(session: AsyncSession, kind: str, target: str) -> None:
    """Queue a job, or move an existing one to the front of the queue."""
    stmt = insert(FetchJob).values(kind=kind, target=target)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[FetchJob.kind, FetchJob.target], set_={"run_after": func.now()}
        )
    )


async def get_job(session: AsyncSession, kind: str, target: str) -> FetchJob | None:
    result = await session.scalars(
        select(FetchJob).where(FetchJob.kind == kind, FetchJob.target == target)
    )
    return result.one_or_none()


async def set_inventory_owner(session: AsyncSession, steam_id64: str | None) -> None:
    """Import the inventory of another account (or stop importing) from now on.

    Positions of the previous account disappear at the next sync, which marks every
    inventory position missing from the new inventory as removed.
    """
    previous = await get_setting(session, SETTING_STEAM_ID)
    if previous and previous != steam_id64:
        await delete_job(session, JOB_INVENTORY, previous)
        await set_setting(session, SETTING_PROFILE, None)
        await set_setting(session, SETTING_INVENTORY_POSITIONS, None)
    await set_setting(session, SETTING_STEAM_ID, steam_id64)
    if steam_id64:
        await run_job_now(session, JOB_INVENTORY, steam_id64)
    else:
        await sync_inventory(session, [])


async def held_quantities(session: AsyncSession, item_ids: list[int]) -> dict[int, int]:
    """Total quantity per item over all active positions."""
    result = await session.execute(
        select(Holding.item_id, func.sum(Holding.quantity))
        .where(Holding.item_id.in_(item_ids), Holding.removed_at.is_(None))
        .group_by(Holding.item_id)
    )
    return {item_id: int(quantity) for item_id, quantity in result}


@dataclass(frozen=True)
class Account:
    """The connected Steam account as the portfolio page shows it."""

    steam_id64: str
    profile: SteamProfile | None
    profile_fetched_at: datetime | None
    positions: int | None
    job: FetchJob | None


async def save_profile(session: AsyncSession, profile: SteamProfile, fetched_at: datetime) -> None:
    await set_setting(
        session,
        SETTING_PROFILE,
        json.dumps({**asdict(profile), "fetched_at": fetched_at.isoformat()}),
    )


async def get_account(session: AsyncSession) -> Account | None:
    steam_id = await get_setting(session, SETTING_STEAM_ID)
    if steam_id is None:
        return None
    profile: SteamProfile | None = None
    fetched_at: datetime | None = None
    if raw := await get_setting(session, SETTING_PROFILE):
        data = json.loads(raw)
        fetched_at = datetime.fromisoformat(data.pop("fetched_at"))
        profile = SteamProfile(**data)
    positions = await get_setting(session, SETTING_INVENTORY_POSITIONS)
    return Account(
        steam_id64=steam_id,
        profile=profile,
        profile_fetched_at=fetched_at,
        positions=int(positions) if positions is not None else None,
        job=await get_job(session, JOB_INVENTORY, steam_id),
    )


async def due_job_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count()).select_from(FetchJob).where(FetchJob.run_after <= func.now())
    )
    return count or 0


async def get_or_create_item(session: AsyncSession, market_hash_name: str) -> Item:
    """An item row for any market name, without starting to collect its prices.

    A new item takes its type, group and icon from the catalog when they are known there.
    """
    await session.execute(
        insert(Item).values(market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name)
    assert item is not None
    if item.item_type is None:
        entry = await session.get(CatalogEntry, market_hash_name)
        if entry is not None and entry.item_type:
            found = SearchResult(
                market_hash_name=market_hash_name,
                item_type=entry.item_type,
                bucket_group_id=entry.bucket_group_id,
                classid=entry.classid,
                icon_url=entry.icon_url,
                sell_listings=entry.sell_listings,
                sell_price_usd_cents=entry.sell_price_usd_cents,
            )
            apply_search_result(item, found, parse_item_traits(market_hash_name, entry.item_type))
    return item


async def portfolio_fingerprint(session: AsyncSession) -> tuple[object, ...]:
    """Changes whenever anything the portfolio valuation depends on changes."""
    result = await session.execute(
        text(
            """
            SELECT
                (SELECT max(fetched_at) FROM price_history),
                (SELECT max(day) FROM fx_rates),
                (SELECT md5(coalesce(string_agg(
                    concat_ws(':', id, quantity, buy_price_usd, removed_at, location),
                    ',' ORDER BY id), ''))
                 FROM holdings)
            """
        )
    )
    return tuple(result.one())


async def load_histories(
    session: AsyncSession, item_ids: list[int]
) -> dict[int, list[StoredPoint]]:
    """load_history() for several items in one query."""
    result = await session.execute(
        select(
            PricePoint.item_id,
            PricePoint.ts,
            PricePoint.resolution,
            PricePoint.price,
            PricePoint.volume,
            PricePoint.currency,
        )
        .where(PricePoint.item_id.in_(item_ids))
        .order_by(PricePoint.item_id, PricePoint.ts)
    )
    histories: dict[int, list[StoredPoint]] = {item_id: [] for item_id in item_ids}
    for item_id, ts, resolution, price, volume, currency in result:
        histories[item_id].append(
            StoredPoint(ts, cast(Resolution, resolution), price, volume, currency)
        )
    return histories
