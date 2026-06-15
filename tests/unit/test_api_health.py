import pytest
from httpx import AsyncClient

from backend.app.main import create_app


@pytest.mark.asyncio
async def test_health_endpoint_reports_ok(api_client: AsyncClient) -> None:
    response = await api_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_expected_api_route_groups_are_registered() -> None:
    app = create_app()

    paths = set(app.openapi()["paths"])

    assert "/auth/register" in paths
    assert "/functions" in paths
    assert "/invocations/{invocation_id}" in paths
    assert "/metrics/summary" in paths
    assert "/workers" in paths
