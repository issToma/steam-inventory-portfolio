"""Database access. Functions take a session and leave committing to the caller."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from typing import cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cs2market.core.parsers import ItemTraits, PriceHistory, Resolution, SearchResult
from cs2market.db.tables import FetchJob, FxRate, Item, PricePoint, WatchlistEntry

JOB_RESOLVE = "resolve"
JOB_HISTORY = "history"
JOB_FX = "fx"

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


async def add_to_watchlist(session: AsyncSession, market_hash_name: str) -> Item:
    """Add an item and queue its jobs. Nothing here talks to Steam: the collector does."""
    await session.execute(
        insert(Item).values(market_hash_name=market_hash_name).on_conflict_do_nothing()
    )
    item = await get_item_by_name(session, market_hash_name)
    assert item is not None
    await session.execute(insert(WatchlistEntry).values(item_id=item.id).on_conflict_do_nothing())
    if item.item_type is None:
        await ensure_job(session, JOB_RESOLVE, market_hash_name)
    await ensure_job(session, JOB_HISTORY, market_hash_name)
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


async def jobs_for(session: AsyncSession, targets: list[str]) -> dict[str, FetchJob]:
    """History jobs by item name, so the UI can show each item's collection status."""
    result = await session.scalars(
        select(FetchJob).where(FetchJob.kind == JOB_HISTORY, FetchJob.target.in_(targets))
    )
    return {job.target: job for job in result}
