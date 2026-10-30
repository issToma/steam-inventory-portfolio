import asyncio
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self, cast

import aiohttp
import pytest

from invinv.core.http import (
    SteamBusyError,
    SteamClient,
    SteamError,
    SteamRateLimitedError,
)


class FakeResponse:
    def __init__(self, status: int, body: bytes, gate: asyncio.Event | None = None) -> None:
        self.status = status
        self._body = body
        self._gate = gate

    async def read(self) -> bytes:
        if self._gate is not None:
            await self._gate.wait()
        return self._body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class FakeSession:
    timeout = None

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any, Any]] = []

    def get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        headers: Any = None,
        **kwargs: Any,
    ) -> FakeResponse:
        self.calls.append((url, params, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(session: FakeSession, clock: FakeClock) -> SteamClient:
    return SteamClient(
        cast(aiohttp.ClientSession, session),
        interval=20,
        cooldown=300,
        max_cooldown=1200,
        clock=clock,
        sleep=clock.sleep,
    )


async def test_requests_are_spaced_by_interval() -> None:
    clock = FakeClock()
    session = FakeSession([FakeResponse(200, b"[1]"), FakeResponse(200, b"[2]")])
    client = make_client(session, clock)

    assert await client.get_json("/a") == [1]
    clock.now += 5
    assert await client.get_json("/b") == [2]

    assert clock.sleeps == [15]
    assert [c[0] for c in session.calls] == [
        "https://steamcommunity.com/a",
        "https://steamcommunity.com/b",
    ]


async def test_429_triggers_growing_cooldown_and_success_resets_it() -> None:
    clock = FakeClock()
    session = FakeSession(
        [FakeResponse(429, b""), FakeResponse(429, b""), FakeResponse(200, b"{}")]
    )
    client = make_client(session, clock)

    with pytest.raises(SteamRateLimitedError) as first:
        await client.get_json("/a")
    assert first.value.retry_at == 1300
    with pytest.raises(SteamRateLimitedError):
        await client.get_json("/a")
    assert clock.sleeps == [300]
    assert client.cooling_down_until == clock.now + 600

    assert await client.get_json("/a") == {}
    assert clock.sleeps == [300, 600]
    assert client.cooling_down_until is None


async def test_server_errors_are_retried() -> None:
    clock = FakeClock()
    session = FakeSession(
        [FakeResponse(502, b""), aiohttp.ClientConnectionError(), FakeResponse(200, b"{}")]
    )
    client = make_client(session, clock)

    assert await client.get_json("/a") == {}
    assert len(session.calls) == 3


async def test_client_errors_are_not_retried() -> None:
    clock = FakeClock()
    session = FakeSession([FakeResponse(400, b"[]")])
    client = make_client(session, clock)

    with pytest.raises(SteamError, match="HTTP 400"):
        await client.get_json("/a")
    assert len(session.calls) == 1


async def test_priority_request_goes_before_waiting_background_requests() -> None:
    clock = FakeClock()
    gate = asyncio.Event()
    session = FakeSession(
        [
            FakeResponse(200, b"0", gate),
            FakeResponse(200, b"1"),
            FakeResponse(200, b"2"),
        ]
    )
    client = make_client(session, clock)
    order: list[str] = []

    async def fetch(path: str, priority: bool) -> None:
        await client.get_json(path, priority=priority)
        order.append(path)

    running = asyncio.create_task(fetch("/running", False))
    await asyncio.sleep(0)
    background = asyncio.create_task(fetch("/background", False))
    await asyncio.sleep(0)
    user = asyncio.create_task(fetch("/user", True))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(running, background, user)

    assert order == ["/running", "/user", "/background"]


async def test_max_wait_fails_fast_during_cooldown() -> None:
    clock = FakeClock()
    session = FakeSession([FakeResponse(429, b"")])
    client = make_client(session, clock)
    with pytest.raises(SteamRateLimitedError):
        await client.get_json("/a")

    with pytest.raises(SteamBusyError):
        await client.get_json("/a", priority=True, max_wait=60)
    assert len(session.calls) == 1
    assert clock.sleeps == []


def adaptive_client(session: FakeSession, clock: FakeClock) -> SteamClient:
    return SteamClient(
        cast(aiohttp.ClientSession, session),
        interval=20,
        min_interval=8,
        cooldown=300,
        clock=clock,
        sleep=clock.sleep,
    )


async def test_adaptive_pace_is_fast_until_a_429_then_slow() -> None:
    clock = FakeClock()
    responses: list[FakeResponse | Exception] = [
        *[FakeResponse(200, b"{}") for _ in range(3)],
        FakeResponse(429, b""),
        *[FakeResponse(200, b"{}") for _ in range(3)],
    ]
    client = adaptive_client(FakeSession(responses), clock)

    for _ in range(3):
        await client.get_json("/a")
    assert client.pace == 8

    with pytest.raises(SteamRateLimitedError):
        await client.get_json("/a")
    for _ in range(3):
        await client.get_json("/a")

    assert client.pace == 20


async def test_fixed_pace_without_min_interval() -> None:
    clock = FakeClock()
    responses: list[FakeResponse | Exception] = [FakeResponse(200, b"{}") for _ in range(30)]
    session = FakeSession(responses)
    client = make_client(session, clock)

    for _ in range(30):
        await client.get_json("/a")

    assert client.pace == 20 and not client.adaptive


async def test_resume_ends_the_cooldown_of_a_waiting_request() -> None:
    session = FakeSession([FakeResponse(429, b""), FakeResponse(200, b"{}")])
    started = asyncio.Event()

    async def slow_sleep(seconds: float) -> None:
        started.set()
        await asyncio.sleep(3600)

    client = SteamClient(
        cast(aiohttp.ClientSession, session), interval=20, cooldown=300, sleep=slow_sleep
    )
    with pytest.raises(SteamRateLimitedError):
        await client.get_json("/a")
    waiting = asyncio.create_task(client.get_json("/a"))
    await started.wait()

    client.resume()

    assert await asyncio.wait_for(waiting, 1) == {}
    assert client.cooling_down_until is None
