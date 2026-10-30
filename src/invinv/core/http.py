"""The only way the app talks to Steam: one rate-limited, strictly sequential client."""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any

import aiohttp
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

STEAM_COMMUNITY = "https://steamcommunity.com"

RATE_LIMITED_MESSAGE = "Steam rate limit hit (HTTP 429)"

# A sequence of pairs allows repeated keys such as category_730_Quality[].
type Params = Mapping[str, str] | Sequence[tuple[str, str]]


class SteamError(Exception):
    """Steam answered with something we cannot use."""


class SteamRateLimitedError(SteamError):
    """Steam answered 429; the client is cooling down until `retry_at` (monotonic time)."""

    def __init__(self, retry_at: float) -> None:
        super().__init__(RATE_LIMITED_MESSAGE)
        self.retry_at = retry_at


class SteamHttpError(SteamError):
    """Steam answered with an unexpected HTTP status."""

    def __init__(self, path: str, status: int) -> None:
        super().__init__(f"{path}: HTTP {status}")
        self.status = status


class SteamBusyError(SteamError):
    """A request would have to wait longer than its caller allows."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"Steam client is busy for another {wait:.0f} s")
        self.wait = wait


class _TransientError(SteamError):
    """A network error or 5xx that is worth retrying."""


class SteamClient:
    """Sends requests one at a time, at least `interval` seconds apart.

    Priority requests (a user waiting in the UI) take the next free slot ahead of
    background ones; they still respect the interval and the 429 cool-down.

    After a 429 the client stops all requests for a cool-down that doubles with every
    consecutive 429 (from `cooldown` up to `max_cooldown`) and resets after a success.
    resume() ends a cool-down early, say after the user switched to another IP address.

    With `min_interval` below `interval` the pace is adaptive: requests go `min_interval`
    apart until Steam answers 429, and `interval` apart from then on.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        interval: float,
        min_interval: float | None = None,
        cooldown: float = 300.0,
        max_cooldown: float = 3600.0,
        base_url: str = STEAM_COMMUNITY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._interval = interval
        self._min_interval = min(min_interval, interval) if min_interval else interval
        self._pace = self._min_interval
        self._cooldown = cooldown
        self._max_cooldown = max_cooldown
        self._base_url = base_url
        self._clock = clock
        self._sleep = sleep
        self._slot = asyncio.Condition()
        self._busy = False
        self._priority_waiting = 0
        self._next_allowed = 0.0
        self._consecutive_429 = 0
        self._wake = asyncio.Event()

    @property
    def cooling_down_until(self) -> float | None:
        return self._next_allowed if self._consecutive_429 else None

    @property
    def pace(self) -> float:
        """Seconds between requests right now."""
        return self._pace

    @property
    def adaptive(self) -> bool:
        return self._min_interval < self._interval

    def resume(self) -> None:
        """End a 429 cool-down now; a 429 right after it pauses for longer, as usual."""
        if self._consecutive_429:
            self._next_allowed = self._clock()
            self._wake.set()
            log.info("cool-down ended by the user")

    async def get_json(
        self,
        path: str,
        params: Params | None = None,
        headers: Mapping[str, str] | None = None,
        *,
        priority: bool = False,
        max_wait: float | None = None,
    ) -> Any:
        """Fetch JSON. With `max_wait`, raise SteamBusyError instead of waiting longer
        than that for the interval or a 429 cool-down."""
        body = await self.get_bytes(path, params, headers, priority=priority, max_wait=max_wait)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise SteamError(f"{path}: response is not JSON") from exc

    async def get_text(
        self,
        path: str,
        params: Params | None = None,
        *,
        priority: bool = False,
        max_wait: float | None = None,
        request_timeout: float | None = None,
    ) -> str:
        """Fetch an HTML or XML page (market pages embed their data in the HTML)."""
        body = await self.get_bytes(
            path, params, priority=priority, max_wait=max_wait, request_timeout=request_timeout
        )
        return body.decode("utf-8", errors="replace")

    async def get_bytes(
        self,
        path: str,
        params: Params | None = None,
        headers: Mapping[str, str] | None = None,
        *,
        priority: bool = False,
        max_wait: float | None = None,
        request_timeout: float | None = None,
    ) -> bytes:
        """With `request_timeout`, override the session's total timeout for this request."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_TransientError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=10, max=60),
            sleep=self._sleep,
            reraise=True,
        ):
            with attempt:
                async with self._turn(priority):
                    return await self._get_once(path, params, headers, max_wait, request_timeout)
        raise AssertionError("unreachable")

    @contextlib.asynccontextmanager
    async def _turn(self, priority: bool) -> AsyncIterator[None]:
        async with self._slot:
            if priority:
                self._priority_waiting += 1
            try:
                await self._slot.wait_for(
                    lambda: not self._busy and (priority or self._priority_waiting == 0)
                )
            finally:
                if priority:
                    self._priority_waiting -= 1
            self._busy = True
        try:
            yield
        finally:
            async with self._slot:
                self._busy = False
                self._slot.notify_all()

    async def _get_once(
        self,
        path: str,
        params: Params | None,
        headers: Mapping[str, str] | None,
        max_wait: float | None,
        request_timeout: float | None = None,
    ) -> bytes:
        delay = self._next_allowed - self._clock()
        if max_wait is not None and delay > max_wait:
            raise SteamBusyError(delay)
        while (delay := self._next_allowed - self._clock()) > 0:
            await self._wait(delay)
        try:
            timeout = (
                self._session.timeout
                if request_timeout is None
                else aiohttp.ClientTimeout(total=request_timeout)
            )
            async with self._session.get(
                self._base_url + path, params=params, headers=headers, timeout=timeout
            ) as response:
                status = response.status
                body = await response.read()
        except (aiohttp.ClientError, TimeoutError) as exc:
            self._next_allowed = self._clock() + self._pace
            raise _TransientError(f"{path}: {exc!r}") from exc

        now = self._clock()
        if status == 429:
            self._consecutive_429 += 1
            pause = min(self._cooldown * 2 ** (self._consecutive_429 - 1), self._max_cooldown)
            self._next_allowed = now + pause
            log.warning("Steam returned 429; pausing all requests for %.0f s", pause)
            self._slow_down()
            raise SteamRateLimitedError(self._next_allowed)

        self._consecutive_429 = 0
        self._next_allowed = now + self._pace
        if status >= 500:
            raise _TransientError(f"{path}: HTTP {status}")
        if status != 200:
            raise SteamHttpError(path, status)
        return body

    async def _wait(self, delay: float) -> None:
        """Sleep for `delay`, or less if resume() is called meanwhile."""
        self._wake.clear()
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waker = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waker.cancel()

    def _slow_down(self) -> None:
        if self._pace < self._interval:
            log.info(
                "pace: %.0f s between requests (%.0f s drew a 429)", self._interval, self._pace
            )
            self._pace = self._interval
