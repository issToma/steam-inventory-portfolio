from fastapi.testclient import TestClient

from cs2market import __version__
from cs2market.main import create_api


def test_health() -> None:
    client = TestClient(create_api())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
