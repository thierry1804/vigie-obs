import pytest
from fastapi.testclient import TestClient

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.main import app


@pytest.fixture
def client():
    with get_session() as session:
        session.add(Tenant(id="tenant_a", name="A", api_token="token_a", mcp_token="mcp_a"))
        session.add(Tenant(id="tenant_b", name="B", api_token="token_b", mcp_token="mcp_b"))
        session.commit()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == "3.0.1"


def test_ask_scoped_tenant(client):
    r = client.post("/ask", json={"question": "test?"}, headers={"X-Tenant-ID": "tenant_a"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant_a"


def test_metrics_usage(client):
    client.post("/ask", json={"question": "q"}, headers={"X-Tenant-ID": "tenant_a"})
    r = client.get("/metrics/usage", headers={"X-Tenant-ID": "tenant_a"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant_a"
