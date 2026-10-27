from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self, cast

import aiohttp
import pytest

from cs2market.core.http import SteamClient, SteamError, SteamRateLimitedError


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    async def read(self) -> bytes:
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
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any, Any]] = []

    def get(
        self, url: str, params: Mapping[str, str] | None = None, headers: Any = None
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
