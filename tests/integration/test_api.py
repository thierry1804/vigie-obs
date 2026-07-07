import re

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
    assert r.json()["version"] == "2.0.0"


def test_ask_scoped_tenant(client):
    r = client.post("/ask", json={"question": "test?"}, headers={"X-Tenant-ID": "tenant_a"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant_a"


def test_metrics_usage(client):
    client.post("/ask", json={"question": "q"}, headers={"X-Tenant-ID": "tenant_a"})
    r = client.get("/metrics/usage", headers={"X-Tenant-ID": "tenant_a"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant_a"


def test_mcp_requires_token(client):
    r = client.post("/mcp/tools/get_project_health", json={"hours": 24})
    assert r.status_code == 401


def test_mcp_health_with_token(client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={"data": {"result": []}},
    )
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": []}},
    )
    r = client.post(
        "/mcp/tools/get_project_health",
        json={"hours": 24},
        headers={"Authorization": "Bearer mcp_a", "X-Tenant-ID": "tenant_a"},
    )
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "tenant_a"


def test_tenant_b_cannot_use_mcp_token_a(client):
    r = client.post(
        "/mcp/tools/get_project_health",
        json={"hours": 24},
        headers={"Authorization": "Bearer mcp_a", "X-Tenant-ID": "tenant_b"},
    )
    assert r.status_code == 403
