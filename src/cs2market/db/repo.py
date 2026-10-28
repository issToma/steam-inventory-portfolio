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

from cs2market.core.games import CS2_APPID
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
    AlertEvent,
    AlertRule,
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


def item_target(appid: int, key: str) -> str:
    """Target of a job about an item or a market group: "<appid>:<name or group id>"."""
    return f"{appid}:{key}"


def split_target(target: str) -> tuple[int, str]:
    appid, _, key = target.partition(":")
    return int(appid), key


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


async def get_item_by_name(
    session: AsyncSession, market_hash_name: str, appid: int = CS2_APPID
) -> Item | None:
    result = await session.scalars(
        select(Item).where(Item.appid == appid, Item.market_hash_name == market_hash_name)
    )
    return result.one_or_none()


async def get_item(session: AsyncSession, item_id: int) -> Item | None:
    return await session.get(Item, item_id)


async def _track(
    session: AsyncSession,
    market_hash_name: str,
    found: SearchResult | None,
    appid: int = CS2_APPID,
) -> Item:
    """Get or create an item and queue the jobs that keep its price history fresh.

    Nothing here talks to Steam: the collector does. `found` is the search result the user
    picked (its game wins over `appid`); with it the item needs no resolve job.
    """
    if found is not None:
        appid = found.appid
    await session.execute(
        insert(Item).values(appid=appid, market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name, appid)
    assert item is not None
    if found is not None:
        apply_search_result(item, found, parse_item_traits(market_hash_name, found.item_type))
    if item.item_type is None:
        await ensure_job(session, JOB_RESOLVE, item_target(appid, market_hash_name))
    await ensure_history_job(session, item)
    return item


async def add_to_watchlist(
    session: AsyncSession,
    market_hash_name: str,
    found: SearchResult | None = None,
    appid: int = CS2_APPID,
) -> Item:
    item = await _track(session, market_hash_name, found, appid)
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
            FetchJob.target == item_target(item.appid, item.market_hash_name),
        )
    )


async def ensure_history_job(session: AsyncSession, item: Item) -> None:
    """Queue history collection: per group when the group is known, per item otherwise."""
    by_name = item_target(item.appid, item.market_hash_name)
    if item.bucket_group_id:
        await ensure_job(session, JOB_GROUP, item_target(item.appid, item.bucket_group_id))
        await delete_job(session, JOB_HISTORY, by_name)
    else:
        await ensure_job(session, JOB_HISTORY, by_name)


def _tracked_condition() -> ColumnElement[bool]:
    """An item is tracked when it is watched or in an active portfolio position."""
    held = select(Holding.id).where(Holding.item_id == Item.id, Holding.removed_at.is_(None))
    return or_(_watched_condition(), held.exists())


def _watched_condition() -> ColumnElement[bool]:
    """In the watchlist or under an enabled alert: the user wants this price fresh."""
    in_watchlist = select(WatchlistEntry.item_id).where(WatchlistEntry.item_id == Item.id)
    alerted = select(AlertRule.id).where(AlertRule.item_id == Item.id, AlertRule.enabled)
    return or_(in_watchlist.exists(), alerted.exists())


async def any_watched(session: AsyncSession, item_ids: list[int]) -> bool:
    found = await session.scalar(
        select(Item.id).where(Item.id.in_(item_ids), _watched_condition()).limit(1)
    )
    return found is not None


async def tracked_items_in_group(
    session: AsyncSession, appid: int, bucket_group_id: str
) -> list[Item]:
    result = await session.scalars(
        select(Item).where(
            Item.appid == appid, Item.bucket_group_id == bucket_group_id, _tracked_condition()
        )
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
    item.name_color = result.name_color or item.name_color
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
        by_target.setdefault(item_target(item.appid, item.market_hash_name), []).append(item)
        if item.bucket_group_id:
            by_target.setdefault(item_target(item.appid, item.bucket_group_id), []).append(item)
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
    appid: int = CS2_APPID
    name_color: str | None = None


async def upsert_catalog(session: AsyncSession, records: list[CatalogRecord]) -> None:
    """Remember items; a record without a price keeps the price we already know."""
    for record in records:
        category = (
            parse_item_traits(record.market_hash_name, record.item_type).category
            if record.item_type
            else None
        )
        values: dict[str, object] = {
            "appid": record.appid,
            "market_hash_name": record.market_hash_name,
            "name_color": record.name_color,
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
            if key not in ("appid", "market_hash_name")
        }
        update_set["seen_at"] = func.now()
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CatalogEntry.appid, CatalogEntry.market_hash_name],
                set_=update_set,
            )
        )


async def search_catalog(
    session: AsyncSession,
    text: str,
    *,
    category: str | None = None,
    quality: str | None = None,
    exterior_name: str | None = None,
    appid: int | None = None,
    limit: int = 20,
) -> list[CatalogEntry]:
    """Typo-tolerant search: names containing every word first, then trigram matches.

    `appid` limits the search to one game; by default every game is searched.
    """
    name = CatalogEntry.market_hash_name
    words = text.split()
    query = select(CatalogEntry)
    if appid is not None:
        query = query.where(CatalogEntry.appid == appid)
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
# The games of the connected inventory (JSON list) and the ones the user switched off.
SETTING_GAMES = "inventory_games"
SETTING_DISABLED_GAMES = "disabled_games"
# Where alerts are sent; the token is a secret and never shown back in full.
SETTING_TELEGRAM_TOKEN = "telegram_token"
SETTING_TELEGRAM_CHAT = "telegram_chat"
SETTING_TELEGRAM_LANGUAGE = "telegram_language"


async def add_manual_holding(
    session: AsyncSession,
    market_hash_name: str,
    *,
    quantity: int,
    location: str | None = None,
    buy_price_usd: Decimal | None = None,
    bought_at: date | None = None,
    found: SearchResult | None = None,
    appid: int = CS2_APPID,
) -> Holding:
    item = await _track(session, market_hash_name, found, appid)
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


async def sync_inventory(
    session: AsyncSession, items: list[InventoryItem], untouched: frozenset[int] = frozenset()
) -> int:
    """Make the inventory positions match the given inventory. Returns the position count.

    Items that cannot be sold on the market are ignored: they have no price. Pinned positions
    keep their quantity when fewer or none of their items are left in the inventory.
    Positions of the games in `untouched` (say, one whose inventory failed to load) are left
    as they are instead of being removed.
    """
    quantities: dict[tuple[int, str], int] = {}
    details: dict[tuple[int, str], InventoryItem] = {}
    for entry in items:
        if entry.marketable:
            key = (entry.appid, entry.market_hash_name)
            quantities[key] = quantities.get(key, 0) + entry.amount
            details[key] = entry

    await upsert_catalog(
        session,
        [
            CatalogRecord(
                market_hash_name=e.market_hash_name,
                item_type=e.item_type,
                bucket_group_id=e.bucket_group_id,
                classid=e.classid,
                icon_url=e.icon_url,
                appid=e.appid,
                name_color=e.name_color,
            )
            for e in details.values()
        ],
    )
    present: set[int] = set()
    for key, quantity in quantities.items():
        entry = details[key]
        name = entry.market_hash_name
        found = SearchResult(
            market_hash_name=name,
            item_type=entry.item_type,
            bucket_group_id=entry.bucket_group_id,
            classid=entry.classid,
            icon_url=entry.icon_url,
            sell_listings=None,
            sell_price_usd_cents=None,
            appid=entry.appid,
            name_color=entry.name_color,
        )
        item = await _track(session, name, found)
        if entry.publisher_fee is not None:
            item.publisher_fee = Decimal(str(entry.publisher_fee))
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
                set_={
                    # A pinned position only grows: items moved to storage stay counted.
                    "quantity": case(
                        (Holding.pinned, func.greatest(Holding.quantity, stmt.excluded.quantity)),
                        else_=stmt.excluded.quantity,
                    ),
                    "removed_at": None,
                },
            )
        )

    stale = select(Holding).where(
        Holding.source == SOURCE_INVENTORY,
        Holding.removed_at.is_(None),
        Holding.pinned.is_(False),
        Holding.item_id.not_in(present),
    )
    if untouched:
        stale = stale.where(
            Holding.item_id.not_in(select(Item.id).where(Item.appid.in_(untouched)))
        )
    gone = await session.scalars(stale)
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


async def get_or_create_item(
    session: AsyncSession, market_hash_name: str, appid: int = CS2_APPID
) -> Item:
    """An item row for any market name, without starting to collect its prices.

    A new item takes its type, group and icon from the catalog when they are known there.
    """
    await session.execute(
        insert(Item).values(appid=appid, market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name, appid)
    assert item is not None
    if item.item_type is None:
        entry = await session.get(CatalogEntry, (appid, market_hash_name))
        if entry is not None and entry.item_type:
            found = SearchResult(
                market_hash_name=market_hash_name,
                item_type=entry.item_type,
                bucket_group_id=entry.bucket_group_id,
                classid=entry.classid,
                icon_url=entry.icon_url,
                sell_listings=entry.sell_listings,
                sell_price_usd_cents=entry.sell_price_usd_cents,
                appid=appid,
                name_color=entry.name_color,
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
                    concat_ws(':', id, quantity, buy_price_usd, removed_at, location, pinned),
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


async def set_pinned(session: AsyncSession, holding_id: int, pinned: bool) -> None:
    await session.execute(
        update(Holding)
        .where(Holding.id == holding_id, Holding.source == SOURCE_INVENTORY)
        .values(pinned=pinned)
    )


async def pin_all(session: AsyncSession, pinned: bool) -> None:
    """Pin or unpin every active inventory position."""
    await session.execute(
        update(Holding)
        .where(Holding.source == SOURCE_INVENTORY, Holding.removed_at.is_(None))
        .values(pinned=pinned)
    )


async def fill_from_catalog(session: AsyncSession, item: Item) -> None:
    """Copy what the catalog knows into an item's missing fields (icon, type, rarity, ...)."""
    if item.icon_url and item.item_type and item.bucket_group_id and item.name_color:
        return
    entry = await session.get(CatalogEntry, (item.appid, item.market_hash_name))
    if entry is None:
        return
    item.icon_url = item.icon_url or entry.icon_url
    item.name_color = item.name_color or entry.name_color
    item.classid = item.classid or entry.classid
    item.bucket_group_id = item.bucket_group_id or entry.bucket_group_id
    if not item.item_type and entry.item_type:
        traits = parse_item_traits(item.market_hash_name, entry.item_type)
        item.item_type = entry.item_type
        item.rarity, item.quality, item.exterior = traits.rarity, traits.quality, traits.exterior


@dataclass(frozen=True)
class InventoryGame:
    appid: int
    name: str
    icon_url: str | None
    items: int


async def save_inventory_games(session: AsyncSession, games: list[InventoryGame]) -> None:
    await set_setting(session, SETTING_GAMES, json.dumps([asdict(g) for g in games]))


async def inventory_games(session: AsyncSession) -> list[InventoryGame]:
    raw = await get_setting(session, SETTING_GAMES)
    return [InventoryGame(**g) for g in json.loads(raw)] if raw else []


async def disabled_games(session: AsyncSession) -> set[int]:
    raw = await get_setting(session, SETTING_DISABLED_GAMES)
    return set(json.loads(raw)) if raw else set()


async def set_game_enabled(session: AsyncSession, appid: int, enabled: bool) -> None:
    """Switch importing a game's inventory on or off; takes effect at the next sync."""
    disabled = await disabled_games(session)
    if enabled:
        disabled.discard(appid)
    else:
        disabled.add(appid)
    await set_setting(session, SETTING_DISABLED_GAMES, json.dumps(sorted(disabled)))


async def games_in_use(session: AsyncSession) -> list[int]:
    """Games of the tracked items (portfolio or watchlist), most items first."""
    result = await session.execute(
        select(Item.appid, func.count())
        .where(_tracked_condition())
        .group_by(Item.appid)
        .order_by(func.count().desc())
    )
    return [appid for appid, _ in result]


# Alerts (docs/SPEC.md, F11).
TELEGRAM_OFF = "off"
TELEGRAM_PENDING = "pending"
TELEGRAM_SENT = "sent"
TELEGRAM_FAILED = "failed"


async def add_alert(
    session: AsyncSession,
    market_hash_name: str,
    kind: str,
    threshold: Decimal,
    window_hours: int | None = None,
    *,
    appid: int = CS2_APPID,
    found: SearchResult | None = None,
    armed: bool = True,
) -> AlertRule:
    """Add a rule; its item's prices are collected from now on, like a watched item's.

    A rule whose condition already holds is added disarmed (`armed=False`): it fires the
    next time the condition starts to hold rather than right away.
    """
    item = await _track(session, market_hash_name, found, appid)
    rule = AlertRule(
        item_id=item.id, kind=kind, threshold=threshold, window_hours=window_hours, armed=armed
    )
    session.add(rule)
    await session.flush()
    return rule


async def get_alert(session: AsyncSession, rule_id: int) -> AlertRule | None:
    return await session.get(AlertRule, rule_id)


async def set_alert_enabled(session: AsyncSession, rule_id: int, enabled: bool) -> None:
    rule = await get_alert(session, rule_id)
    if rule is None:
        return
    rule.enabled = enabled
    await session.flush()
    item = await get_item(session, rule.item_id)
    if item is None:
        return
    if enabled:
        await _track(session, item.market_hash_name, None, item.appid)
    elif not await is_tracked(session, item.id):
        await _drop_item_jobs(session, item)


async def delete_alert(session: AsyncSession, rule_id: int) -> None:
    rule = await get_alert(session, rule_id)
    if rule is None:
        return
    item = await get_item(session, rule.item_id)
    await session.delete(rule)
    await session.flush()
    if item is not None and not await is_tracked(session, item.id):
        await _drop_item_jobs(session, item)


async def alert_rules(
    session: AsyncSession, item_id: int | None = None
) -> list[tuple[AlertRule, Item]]:
    query = select(AlertRule, Item).join(Item, AlertRule.item_id == Item.id)
    if item_id is not None:
        query = query.where(AlertRule.item_id == item_id)
    result = await session.execute(query.order_by(Item.market_hash_name, AlertRule.id))
    return [(rule, item) for rule, item in result]


async def enabled_alerts_for(session: AsyncSession, item_ids: list[int]) -> list[AlertRule]:
    result = await session.scalars(
        select(AlertRule).where(AlertRule.item_id.in_(item_ids), AlertRule.enabled)
    )
    return list(result)


async def add_alert_event(
    session: AsyncSession,
    rule: AlertRule,
    price_usd: float,
    change: float | None,
    *,
    telegram: str,
) -> AlertEvent:
    event = AlertEvent(
        rule_id=rule.id,
        item_id=rule.item_id,
        kind=rule.kind,
        threshold=rule.threshold,
        window_hours=rule.window_hours,
        price_usd=Decimal(str(round(price_usd, 4))),
        change=change,
        telegram=telegram,
    )
    session.add(event)
    await session.flush()
    return event


async def alert_events(session: AsyncSession, limit: int = 100) -> list[tuple[AlertEvent, Item]]:
    result = await session.execute(
        select(AlertEvent, Item)
        .join(Item, AlertEvent.item_id == Item.id)
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
        .limit(limit)
    )
    return [(event, item) for event, item in result]


async def unread_alert_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count()).select_from(AlertEvent).where(AlertEvent.read_at.is_(None))
    )
    return count or 0


async def mark_alerts_read(session: AsyncSession) -> None:
    await session.execute(
        update(AlertEvent).where(AlertEvent.read_at.is_(None)).values(read_at=func.now())
    )


async def pending_telegram_events(
    session: AsyncSession, limit: int = 20
) -> list[tuple[AlertEvent, Item]]:
    result = await session.execute(
        select(AlertEvent, Item)
        .join(Item, AlertEvent.item_id == Item.id)
        .where(AlertEvent.telegram == TELEGRAM_PENDING)
        .order_by(AlertEvent.id)
        .limit(limit)
    )
    return [(event, item) for event, item in result]


async def set_telegram_status(session: AsyncSession, event_id: int, status: str) -> None:
    await session.execute(
        update(AlertEvent).where(AlertEvent.id == event_id).values(telegram=status)
    )


@dataclass(frozen=True)
class TelegramSettings:
    token: str
    chat_id: str
    language: str


async def telegram_settings(session: AsyncSession) -> TelegramSettings | None:
    """Where to send alerts, or None while Telegram is not fully set up."""
    token = await get_setting(session, SETTING_TELEGRAM_TOKEN)
    chat_id = await get_setting(session, SETTING_TELEGRAM_CHAT)
    if not token or not chat_id:
        return None
    language = await get_setting(session, SETTING_TELEGRAM_LANGUAGE) or "en"
    return TelegramSettings(token=token, chat_id=chat_id, language=language)
