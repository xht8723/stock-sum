"""HTTP API smoke tests."""

from fastapi.testclient import TestClient

from stock_sum.api.app import create_app


def test_health_route() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
