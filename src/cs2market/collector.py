"""Background worker that runs due fetch_jobs one by one through the shared Steam client."""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cs2market.core.currency import USD
from cs2market.core.http import SteamError, SteamRateLimitedError
from cs2market.core.parsers import SteamFormatError, parse_item_traits
from cs2market.core.steam import SteamMarket
from cs2market.db import repo
from cs2market.db.session import SessionMaker
from cs2market.db.tables import FetchJob
from cs2market.settings import Settings

log = logging.getLogger(__name__)

IDLE_POLL = timedelta(seconds=30)
RETRY_BASE = timedelta(minutes=5)
RETRY_MAX = timedelta(hours=6)
NOT_FOUND_RETRY = timedelta(days=7)


class JobFailedError(Exception):
    """A job failed in a way that retrying soon will not fix."""


class Collector:
    def __init__(
        self,
        sessions: SessionMaker,
        market: SteamMarket,
        settings: Settings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._market = market
        self._settings = settings
        self._now = now

    async def run_forever(self) -> None:
        log.info("collector started")
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(await self._idle_seconds())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("collector loop error")
                await asyncio.sleep(IDLE_POLL.total_seconds())

    async def run_once(self) -> bool:
        """Run the next due job. Returns False when nothing was due."""
        async with self._sessions() as session:
            job = await repo.next_due_job(session, self._now())
        if job is None:
            return False
        await self._run(job)
        return True

    async def _idle_seconds(self) -> float:
        async with self._sessions() as session:
            next_run = await repo.next_run_time(session)
        if next_run is None:
            return IDLE_POLL.total_seconds()
        wait = (next_run - self._now()).total_seconds()
        return min(max(wait, 1.0), IDLE_POLL.total_seconds())

    async def _run(self, job: FetchJob) -> None:
        handlers = {
            repo.JOB_RESOLVE: self._resolve,
            repo.JOB_HISTORY: self._history,
            repo.JOB_FX: self._fx,
        }
        handler = handlers.get(job.kind)
        try:
            if handler is None:
                raise JobFailedError(f"unknown job kind {job.kind!r}")
            next_run = await handler(job)
        except SteamRateLimitedError as exc:
            retry_at = self._now() + timedelta(seconds=max(exc.retry_at - time.monotonic(), 0))
            await self._fail(job, str(exc), retry_at, count_attempt=False)
        except JobFailedError as exc:
            await self._fail(job, str(exc), self._now() + NOT_FOUND_RETRY)
        except (SteamError, SteamFormatError) as exc:
            backoff = min(RETRY_BASE * 2**job.attempts, RETRY_MAX)
            log.warning("%s %r failed: %s", job.kind, job.target, exc)
            await self._fail(job, str(exc), self._now() + backoff)
        else:
            async with self._sessions() as session:
                await repo.finish_job(session, job, next_run)
                await session.commit()

    async def _fail(
        self, job: FetchJob, error: str, retry_at: datetime, *, count_attempt: bool = True
    ) -> None:
        async with self._sessions() as session:
            await repo.fail_job(session, job, error, retry_at, count_attempt=count_attempt)
            await session.commit()

    async def _resolve(self, job: FetchJob) -> datetime | None:
        results = await self._market.search(job.target)
        match = next((r for r in results if r.market_hash_name == job.target), None)
        if match is None:
            raise JobFailedError("item not found on the market; check the exact name")
        async with self._sessions() as session:
            item = await repo.get_item_by_name(session, job.target)
            if item is not None:
                repo.apply_search_result(
                    item, match, parse_item_traits(job.target, match.item_type)
                )
            await session.commit()
        return None

    async def _history(self, job: FetchJob) -> datetime | None:
        async with self._sessions() as session:
            item = await repo.get_item_by_name(session, job.target)
            if item is None or not await repo.is_watched(session, item.id):
                return None
        history = await self._market.price_history(job.target)
        async with self._sessions() as session:
            await repo.save_history(session, item.id, history)
            if history.currency != USD:
                await repo.ensure_job(session, repo.JOB_FX, str(history.currency))
            await session.commit()
        log.info("stored %d points for %r", len(history.points), job.target)
        return self._now() + timedelta(minutes=self._settings.history_refresh_minutes)

    async def _fx(self, job: FetchJob) -> datetime | None:
        """Derive Steam's own rate from one item priced in USD and in the local currency."""
        currency = int(job.target)
        reference = self._settings.fx_reference_item
        usd = await self._market.price_overview(reference, USD)
        local = await self._market.price_overview(reference, currency)
        if not usd.lowest_price or not local.lowest_price:
            raise SteamError(f"no lowest price for {reference!r}")
        rate = usd.lowest_price / local.lowest_price
        async with self._sessions() as session:
            await repo.save_fx(session, self._now().date(), currency, rate)
            await session.commit()
        log.info("Steam rate for currency %d: %s USD", currency, rate)
        return self._now() + timedelta(hours=self._settings.fx_refresh_hours)
