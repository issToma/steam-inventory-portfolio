"""Background worker that runs due fetch_jobs one by one through the shared Steam client."""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from invinv import alerts, scheduling
from invinv.core.currency import USD
from invinv.core.games import CS2_APPID
from invinv.core.http import SteamError, SteamRateLimitedError
from invinv.core.parsers import (
    InventoryItem,
    PriceHistory,
    ProfileRef,
    SteamFormatError,
    parse_item_traits,
)
from invinv.core.steam import InventoryPrivateError, SteamMarket
from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.db.tables import FetchJob
from invinv.settings import Settings

log = logging.getLogger(__name__)

IDLE_POLL = timedelta(seconds=30)
RETRY_BASE = timedelta(minutes=5)
RETRY_MAX = timedelta(hours=6)
NOT_FOUND_RETRY = timedelta(days=7)
PRIVATE_INVENTORY_RETRY = timedelta(hours=1)
# Name and avatar change rarely; refresh them along with one inventory sync a day.
PROFILE_REFRESH = timedelta(days=1)
# Price histories of items nobody watches: for charts and period changes only.
HISTORY_REFRESH = timedelta(days=1)


class JobFailedError(Exception):
    """A job failed in a way that retrying soon will not fix."""


class Collector:
    def __init__(
        self,
        sessions: SessionMaker,
        market: SteamMarket,
        settings: Settings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        notifier: alerts.Notifier | None = None,
    ) -> None:
        self._sessions = sessions
        self._market = market
        self._settings = settings
        self._now = now
        self._notifier = notifier

    async def run_forever(self) -> None:
        log.info("collector started")
        while True:
            try:
                await self._notify()
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

    async def _notify(self) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.deliver(self._now())
        except Exception:
            log.exception("alert delivery failed")

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
            repo.JOB_GROUP: self._group,
            repo.JOB_INVENTORY: self._inventory,
            repo.JOB_FX: self._fx,
            repo.JOB_LISTING: self._listing,
        }
        handler = handlers.get(job.kind)
        try:
            if handler is None:
                raise JobFailedError(f"unknown job kind {job.kind!r}")
            next_run = await handler(job)
        except SteamRateLimitedError as exc:
            retry_at = self._now() + timedelta(seconds=max(exc.retry_at - time.monotonic(), 0))
            await self._fail(job, str(exc), retry_at, count_attempt=False)
        except InventoryPrivateError:
            await self._fail(
                job,
                "the inventory is not public: open it in Steam's privacy settings",
                self._now() + PRIVATE_INVENTORY_RETRY,
            )
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
        appid, name = repo.split_target(job.target)
        results = await self._market.search(name, appid=appid)
        match = next((r for r in results if r.market_hash_name == name), None)
        if match is None:
            # No point fetching history for a name the market does not know.
            async with self._sessions() as session:
                await repo.delete_job(session, repo.JOB_HISTORY, job.target)
                await session.commit()
            raise JobFailedError("item not found on the market; check the exact name")
        async with self._sessions() as session:
            item = await repo.get_item_by_name(session, name, appid)
            if item is not None:
                repo.apply_search_result(item, match, parse_item_traits(name, match.item_type))
                if await repo.is_tracked(session, item.id):
                    await repo.ensure_history_job(session, item)
            await session.commit()
        return None

    async def _history(self, job: FetchJob) -> datetime | None:
        appid, name = repo.split_target(job.target)
        async with self._sessions() as session:
            item = await repo.get_item_by_name(session, name, appid)
            if item is None or not await repo.is_tracked(session, item.id):
                return None
            if item.bucket_group_id:
                # Older per-item job: hand over to the group job, which is cheaper.
                await repo.ensure_history_job(session, item)
                await session.commit()
                return None
        history = await self._market.price_history(name, appid)
        async with self._sessions() as session:
            await repo.save_history(session, item.id, history)
            stored = await repo.get_item(session, item.id)
            if stored is not None:
                await repo.fill_from_catalog(session, stored)
            if history.currency != USD:
                await repo.ensure_job(session, repo.JOB_FX, str(history.currency))
            await alerts.check_items(session, [item.id], self._now())
            await session.commit()
        log.info("stored %d points for %r", len(history.points), job.target)
        return await self._next_history_refresh([item.id])

    async def _group(self, job: FetchJob) -> datetime | None:
        appid, group = repo.split_target(job.target)
        async with self._sessions() as session:
            items = await repo.tracked_items_in_group(session, appid, group)
        if not items:
            return None
        if len(items) == 1:
            # One tracked variant: the per-item JSON is the same single request as the group
            # page, but answers in seconds instead of tens of seconds.
            (item,) = items
            single = await self._market.price_history(item.market_hash_name, appid)
            async with self._sessions() as session:
                await repo.save_history(session, item.id, single)
                stored = await repo.get_item(session, item.id)
                if stored is not None:
                    await repo.fill_from_catalog(session, stored)
                if single.currency != USD:
                    await repo.ensure_job(session, repo.JOB_FX, str(single.currency))
                await alerts.check_items(session, [item.id], self._now())
                await session.commit()
            next_run = await self._next_history_refresh([item.id])
            log.info(
                "stored history for %r; next refresh %s",
                item.market_hash_name,
                f"{next_run:%Y-%m-%d %H:%M} UTC",
            )
            return next_run
        variants = await self._market.group_page(group, appid)
        async with self._sessions() as session:
            # Every variant goes to the catalog, so local search knows all wears at once.
            await repo.upsert_catalog(
                session,
                [
                    repo.CatalogRecord(
                        market_hash_name=v.market_hash_name,
                        item_type=v.item_type,
                        bucket_group_id=group,
                        classid=v.classid,
                        icon_url=v.icon_url,
                        appid=appid,
                        name_color=v.name_color,
                    )
                    for v in variants.values()
                ],
            )
            await session.commit()
        currencies = set()
        fetched: list[tuple[int, PriceHistory]] = []
        for item in items:
            variant = variants.get(item.market_hash_name)
            history = variant.history if variant else None
            if history is None:
                # Not embedded in the page: fall back to the per-item endpoint.
                history = await self._market.price_history(item.market_hash_name, appid)
            async with self._sessions() as session:
                await repo.save_history(session, item.id, history)
                stored = await repo.get_item(session, item.id)
                if stored is not None:
                    if variant is not None and variant.collection:
                        stored.collection = variant.collection
                    await repo.fill_from_catalog(session, stored)
                await session.commit()
            currencies.add(history.currency)
            fetched.append((item.id, history))
        async with self._sessions() as session:
            for currency in currencies - {USD}:
                await repo.ensure_job(session, repo.JOB_FX, str(currency))
            await alerts.check_items(session, [item_id for item_id, _ in fetched], self._now())
            await session.commit()
        next_run = await self._next_history_refresh([item_id for item_id, _ in fetched])
        log.info(
            "stored history for %d item(s) from group %s; next refresh %s",
            len(items),
            job.target,
            f"{next_run:%Y-%m-%d %H:%M} UTC",
        )
        return next_run

    async def _next_history_refresh(self, item_ids: list[int]) -> datetime:
        """Watched items keep a fresh history for their charts and change alerts; for the
        rest a daily refresh is enough, since their price comes from the listing job."""
        async with self._sessions() as session:
            watched = await repo.any_watched(session, item_ids)
        if watched:
            return self._now() + timedelta(minutes=self._settings.history_refresh_minutes)
        return self._now() + HISTORY_REFRESH

    async def _listing(self, job: FetchJob) -> datetime | None:
        """The cheapest listing of an item, asked in USD: its current price."""
        appid, name = repo.split_target(job.target)
        async with self._sessions() as session:
            item = await repo.get_item_by_name(session, name, appid)
            if item is None or not await repo.is_tracked(session, item.id):
                return None
            quantity = (await repo.held_quantities(session, [item.id])).get(item.id, 0)
            watched = await repo.any_watched(session, [item.id])
        overview = await self._market.price_overview(name, USD, appid)
        now = self._now()
        async with self._sessions() as session:
            await repo.save_listing(session, item.id, overview, now)
            await alerts.check_items(session, [item.id], now)
            await session.commit()
        importance = scheduling.Importance(
            watched=watched,
            # Nothing listed: nothing to watch closely until someone lists it.
            held_value_usd=float(overview.lowest_price) * quantity
            if overview.lowest_price is not None
            else 0.0,
            sales_per_day=float(overview.volume or 0),
        )
        base = timedelta(minutes=self._settings.history_refresh_minutes)
        return now + scheduling.refresh_interval(importance, base)

    async def _inventory(self, job: FetchJob) -> datetime | None:
        async with self._sessions() as session:
            account = await repo.get_account(session)
        if account is None or account.steam_id64 != job.target:
            return None  # the user switched to another account or disconnected
        fetched = account.profile_fetched_at
        stale = fetched is None or self._now() - fetched >= PROFILE_REFRESH
        # A private profile is re-checked every attempt, so the import starts as soon as the
        # user opens their inventory.
        if stale or (account.profile is not None and account.profile.privacy != "public"):
            profile = await self._market.profile(ProfileRef(steam_id64=job.target))
            async with self._sessions() as session:
                await repo.save_profile(session, profile, self._now())
                await session.commit()
            if profile.privacy != "public":
                raise InventoryPrivateError(job.target)
        apps = await self._market.inventory_apps(job.target)
        async with self._sessions() as session:
            await repo.save_inventory_games(
                session,
                [
                    repo.InventoryGame(a.appid, a.name, a.icon_url, sum(a.contexts.values()))
                    for a in apps
                ],
            )
            disabled = await repo.disabled_games(session)
            await session.commit()
        items: list[InventoryItem] = []
        failed: set[int] = set()
        for app in apps:
            if app.appid in disabled:
                continue  # its positions are removed below, like any item that left
            for contextid, count in app.contexts.items():
                if count <= 0:
                    continue
                try:
                    items += await self._market.inventory(job.target, app.appid, contextid)
                except SteamRateLimitedError:
                    raise
                except (SteamError, SteamFormatError) as exc:
                    log.warning("inventory of %s (%d) failed: %s", app.name, app.appid, exc)
                    failed.add(app.appid)
        sellable: dict[int, int] = {}
        for entry in items:
            if entry.marketable:
                sellable[entry.appid] = sellable.get(entry.appid, 0) + entry.amount
        async with self._sessions() as session:
            await repo.save_inventory_games(
                session,
                [
                    repo.InventoryGame(
                        a.appid,
                        a.name,
                        a.icon_url,
                        sum(a.contexts.values()),
                        # Unknown for games not read this time (switched off or failed).
                        None
                        if a.appid in disabled or a.appid in failed
                        else sellable.get(a.appid, 0),
                    )
                    for a in apps
                ],
            )
            count = await repo.sync_inventory(session, items, untouched=frozenset(failed))
            await repo.set_setting(session, repo.SETTING_INVENTORY_POSITIONS, str(count))
            await session.commit()
        log.info(
            "inventory: %d item(s) in %d game(s), %d sellable position(s)",
            len(items),
            len(apps),
            count,
        )
        return self._now() + timedelta(hours=self._settings.inventory_refresh_hours)

    async def _fx(self, job: FetchJob) -> datetime | None:
        """Derive Steam's own rate from one item priced in USD and in the local currency."""
        currency = int(job.target)
        reference = self._settings.fx_reference_item
        # The reference is a CS2 item; Steam's rates are the same for every game.
        usd = await self._market.price_overview(reference, USD, CS2_APPID)
        local = await self._market.price_overview(reference, currency, CS2_APPID)
        if not usd.lowest_price or not local.lowest_price:
            raise SteamError(f"no lowest price for {reference!r}")
        rate = usd.lowest_price / local.lowest_price
        async with self._sessions() as session:
            await repo.save_fx(session, self._now().date(), currency, rate)
            await session.commit()
        log.info("Steam rate for currency %d: %s USD", currency, rate)
        return self._now() + timedelta(hours=self._settings.fx_refresh_hours)
