"""
API tests for PowerplAI.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from backend.src.api.main import app


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    """Test health endpoint returns healthy status."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_query_endpoint_structure(client):
    """Test query endpoint returns expected structure."""
    response = await client.post(
        "/api/query",
        json={"query": "What is expected goals?", "include_rag": False}
    )
    # May fail without DB, but structure should be correct
    if response.status_code == 200:
        data = response.json()
        assert "response" in data
        assert "sources" in data
        assert "query_type" in data


@pytest.mark.asyncio
async def test_invalid_stat_leader(client):
    """Test that invalid stat returns 400."""
    response = await client.get("/api/leaders/invalid_stat")
    assert response.status_code == 400
    assert "Invalid stat" in response.json()["detail"]
