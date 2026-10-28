from typing import Any

import pytest
from fastapi.testclient import TestClient

from cs2market import __version__
from cs2market.main import create_api
from cs2market.runtime import runtime


def test_health() -> None:
    client = TestClient(create_api())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_search_rejects_unknown_filters() -> None:
    client = TestClient(create_api())

    assert client.get("/api/search", params={"q": "x", "kind": "spaceship"}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "exterior": "new"}).status_code == 422


def test_search_passes_all_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeMarket:
        async def search(self, query: str, **kwargs: Any) -> list[Any]:
            calls.append({"query": query, **kwargs})
            return []

    monkeypatch.setattr(runtime, "_market", FakeMarket())
    client = TestClient(create_api())

    response = client.get(
        "/api/search",
        params={"q": "doppler", "kind": "knife", "quality": "stattrak", "exterior": "fn"},
    )

    assert response.status_code == 200
    assert calls[0]["query"] == "doppler"
    assert (calls[0]["kind"], calls[0]["quality"], calls[0]["exterior"]) == (
        "knife",
        "stattrak",
        "fn",
    )
