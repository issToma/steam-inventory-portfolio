from typing import Any

import pytest
from fastapi.testclient import TestClient

from invinv import __version__, api
from invinv.main import create_api
from invinv.search import SearchQuery


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
    calls: list[SearchQuery] = []

    async def fake_search(query: SearchQuery) -> list[Any]:
        calls.append(query)
        return []

    monkeypatch.setattr(api, "search_steam", fake_search)
    client = TestClient(create_api())

    response = client.get(
        "/api/search",
        params={
            "q": "doppler",
            "appid": 730,
            "kind": "knife",
            "quality": "stattrak",
            "exterior": "fn",
        },
    )

    assert response.status_code == 200
    assert calls == [
        SearchQuery("doppler", appid=730, kind="knife", quality="stattrak", exterior="fn")
    ]


def test_steam_search_needs_a_game() -> None:
    client = TestClient(create_api())

    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_search_rejects_unknown_source() -> None:
    client = TestClient(create_api())

    assert client.get("/api/search", params={"q": "x", "source": "web"}).status_code == 422
