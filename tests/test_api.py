from fastapi.testclient import TestClient

from cs2market import __version__
from cs2market.main import create_api


def test_health() -> None:
    client = TestClient(create_api())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_search_rejects_unknown_filters() -> None:
    client = TestClient(create_api())

    assert client.get("/api/search", params={"q": "x", "kind": "spaceship"}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "exterior": "new"}).status_code == 422
