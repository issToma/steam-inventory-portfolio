"""The only way the app talks to Steam: one rate-limited, strictly sequential client."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
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


class SteamError(Exception):
    """Steam answered with something we cannot use."""


class SteamRateLimitedError(SteamError):
    """Steam answered 429; the client is cooling down until `retry_at` (monotonic time)."""

    def __init__(self, retry_at: float) -> None:
        super().__init__("Steam rate limit hit (HTTP 429)")
        self.retry_at = retry_at


class _TransientError(SteamError):
    """A network error or 5xx that is worth retrying."""


class SteamClient:
    """Sends requests one at a time, at least `interval` seconds apart.

    After a 429 the client stops all requests for a cool-down that doubles with every
    consecutive 429 (from `cooldown` up to `max_cooldown`) and resets after a success.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        interval: float,
        cooldown: float = 300.0,
        max_cooldown: float = 3600.0,
        base_url: str = STEAM_COMMUNITY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._interval = interval
        self._cooldown = cooldown
        self._max_cooldown = max_cooldown
        self._base_url = base_url
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._consecutive_429 = 0

    @property
    def cooling_down_until(self) -> float | None:
        return self._next_allowed if self._consecutive_429 else None

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_TransientError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=10, max=60),
            sleep=self._sleep,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(path, params, headers)
        raise AssertionError("unreachable")

    async def _get_once(
        self,
        path: str,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> Any:
        async with self._lock:
            delay = self._next_allowed - self._clock()
            if delay > 0:
                await self._sleep(delay)
            try:
                async with self._session.get(
                    self._base_url + path, params=params, headers=headers
                ) as response:
                    status = response.status
                    body = await response.read()
            except (aiohttp.ClientError, TimeoutError) as exc:
                self._next_allowed = self._clock() + self._interval
                raise _TransientError(f"{path}: {exc!r}") from exc

            now = self._clock()
            if status == 429:
                self._consecutive_429 += 1
                pause = min(self._cooldown * 2 ** (self._consecutive_429 - 1), self._max_cooldown)
                self._next_allowed = now + pause
                log.warning("Steam returned 429; pausing all requests for %.0f s", pause)
                raise SteamRateLimitedError(self._next_allowed)

            self._consecutive_429 = 0
            self._next_allowed = now + self._interval
            if status >= 500:
                raise _TransientError(f"{path}: HTTP {status}")
            if status != 200:
                raise SteamError(f"{path}: HTTP {status}")
            try:
                return json.loads(body)
            except ValueError as exc:
                raise SteamError(f"{path}: response is not JSON") from exc
