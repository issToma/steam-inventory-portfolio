"""Database access. Functions take a session and leave committing to the caller."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from typing import Any, cast

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cs2market.core.parsers import (
    ItemTraits,
    PriceHistory,
    Resolution,
    SearchResult,
    parse_item_traits,
)
from cs2market.db.tables import (
    CatalogEntry,
    FetchJob,
    FxRate,
    Item,
    PricePoint,
    WatchlistEntry,
)

JOB_RESOLVE = "resolve"
JOB_HISTORY = "history"
# One market group page holds the history of every variant in the group (docs/SPEC.md, S6).
JOB_GROUP = "group"
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


async def add_to_watchlist(
    session: AsyncSession, market_hash_name: str, found: SearchResult | None = None
) -> Item:
    """Add an item and queue its jobs. Nothing here talks to Steam: the collector does.

    `found` is the search result the user picked; with it the item needs no resolve job.
    """
    await session.execute(
        insert(Item).values(market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name)
    assert item is not None
    if found is not None:
        apply_search_result(item, found, parse_item_traits(market_hash_name, found.item_type))
    await session.execute(insert(WatchlistEntry).values(item_id=item.id).on_conflict_do_nothing())
    if item.item_type is None:
        await ensure_job(session, JOB_RESOLVE, market_hash_name)
    await ensure_history_job(session, item)
    return item


async def remove_from_watchlist(session: AsyncSession, item_id: int) -> None:
    item = await get_item(session, item_id)
    if item is None:
        return
    await session.execute(delete(WatchlistEntry).where(WatchlistEntry.item_id == item_id))
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


async def watched_items_in_group(session: AsyncSession, bucket_group_id: str) -> list[Item]:
    result = await session.scalars(
        select(Item).join(WatchlistEntry).where(Item.bucket_group_id == bucket_group_id)
    )
    return list(result)


async def is_watched(session: AsyncSession, item_id: int) -> bool:
    return await session.get(WatchlistEntry, item_id) is not None


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
        for ts, resolution, price, volume, currency in result.tuples()
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
        .distinct(FxRate.currency)
        .order_by(FxRate.currency, FxRate.day.desc())
    )
    return {currency: rate for currency, rate in result.tuples()}


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
