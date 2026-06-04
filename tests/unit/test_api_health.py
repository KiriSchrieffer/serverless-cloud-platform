from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_health_endpoint_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_expected_api_route_groups_are_registered() -> None:
    app = create_app()

    paths = {route.path for route in app.routes}

    assert "/auth/register" in paths
    assert "/functions" in paths
    assert "/invocations/{invocation_id}" in paths
    assert "/metrics/summary" in paths
    assert "/workers" in paths
