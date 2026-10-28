"""Process-wide resources: database engine, Steam client and the collector task."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

import aiohttp

from cs2market.collector import Collector
from cs2market.core.http import SteamClient
from cs2market.core.steam import SteamMarket
from cs2market.db import repo
from cs2market.db.session import SessionMaker, make_engine, make_sessionmaker
from cs2market.settings import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectorStatus:
    enabled: bool
    queued: int  # jobs due now
    paused_seconds: float | None  # set while Steam's rate limit pauses all requests


class Runtime:
    def __init__(self) -> None:
        self._sessions: SessionMaker | None = None
        self._http: aiohttp.ClientSession | None = None
        self._collector: asyncio.Task[None] | None = None
        self._market: SteamMarket | None = None
        self._client: SteamClient | None = None
        self._collector_enabled = False

    @property
    def sessions(self) -> SessionMaker:
        if self._sessions is None:
            raise RuntimeError("runtime is not started")
        return self._sessions

    @property
    def market(self) -> SteamMarket:
        """Shared Steam access; UI callers should pass priority=True and a max_wait."""
        if self._market is None:
            raise RuntimeError("runtime is not started")
        return self._market

    async def start(self) -> None:
        settings = get_settings()
        self._engine = make_engine(settings.database_url)
        self._sessions = make_sessionmaker(self._engine)
        self._http = aiohttp.ClientSession(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.8"},
            timeout=aiohttp.ClientTimeout(total=60),
        )
        client = SteamClient(self._http, interval=settings.request_interval_seconds)
        self._client = client
        self._market = SteamMarket(client)
        self._collector_enabled = settings.collector_enabled
        if settings.collector_enabled:
            collector = Collector(self._sessions, self._market, settings)
            self._collector = asyncio.create_task(collector.run_forever())
        else:
            log.info("collector disabled by settings")

    async def collector_status(self) -> CollectorStatus:
        async with self.sessions() as session:
            queued = await repo.due_job_count(session)
        until = self._client.cooling_down_until if self._client else None
        paused = until - time.monotonic() if until is not None else None
        return CollectorStatus(
            enabled=self._collector_enabled,
            queued=queued,
            paused_seconds=paused if paused and paused > 0 else None,
        )

    async def stop(self) -> None:
        if self._collector is not None:
            self._collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._collector
        if self._http is not None:
            await self._http.close()
        if self._sessions is not None:
            await self._engine.dispose()


runtime = Runtime()
