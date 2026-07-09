"""Test client MCP simulant proto-factory."""

import re

import pytest
from fastapi.testclient import TestClient

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.main import app


@pytest.fixture
def mcp_client():
    with get_session() as session:
        t = session.get(Tenant, "default")
        if t:
            t.mcp_token = "test-mcp"
            session.commit()
    with TestClient(app) as c:
        yield c


def test_proto_factory_get_project_health(mcp_client, httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={"data": {"result": []}},
    )
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": []}},
    )
    r = mcp_client.post(
        "/mcp/tools/get_project_health",
        json={"hours": 24},
        headers={"Authorization": "Bearer test-mcp"},
    )
    assert r.status_code == 200
    assert "status" in r.json()


def test_proto_factory_explain_anomaly(mcp_client):
    r = mcp_client.post(
        "/mcp/tools/explain_anomaly",
        json={"question": "Pic CPU hier ?"},
        headers={"Authorization": "Bearer test-mcp"},
    )
    assert r.status_code == 200
    assert "diagnosis" in r.json()


def test_proto_factory_explain_anomaly_uses_ask_preset(mcp_client, monkeypatch):
    import agent.mcp.server as mcp_server_module

    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["endpoint"] = endpoint
        return "diagnostic factice"

    monkeypatch.setattr(mcp_server_module, "run_agent", fake_run_agent)

    r = mcp_client.post(
        "/mcp/tools/explain_anomaly",
        json={"question": "Pic CPU hier ?"},
        headers={"Authorization": "Bearer test-mcp"},
    )

    assert r.status_code == 200
    assert r.json()["diagnosis"] == "diagnostic factice"
    assert captured == {"preset": "ask", "endpoint": "mcp/explain_anomaly"}
