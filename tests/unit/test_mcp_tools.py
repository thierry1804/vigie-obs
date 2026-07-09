import re

import pytest

import agent.mcp.tools as mcp_tools
from agent.db.models import Anomaly
from agent.db.session import get_session


@pytest.fixture(autouse=True)
def _fixed_tenant(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_current_tenant_id", lambda: "acme")


@pytest.mark.asyncio
async def test_get_project_health_returns_status(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={"data": {"result": []}},
    )
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": []}},
    )
    result = await mcp_tools.get_project_health(hours=24)
    assert result["tenant_id"] == "acme"
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_query_incidents_scoped_to_tenant():
    with get_session() as session:
        session.add(Anomaly(tenant_id="acme", signature="s1", title="Acme only", status="open"))
        session.add(Anomaly(tenant_id="other", signature="s2", title="Other only", status="open"))
        session.commit()

    result = await mcp_tools.query_incidents(hours=168)

    titles = [i["title"] for i in result["incidents"]]
    assert "Acme only" in titles
    assert "Other only" not in titles


@pytest.mark.asyncio
async def test_get_business_kpis_empty_without_taxonomy():
    result = await mcp_tools.get_business_kpis(hours=24)
    assert result["tenant_id"] == "acme"
    assert result["kpis"] == {}


@pytest.mark.asyncio
async def test_explain_anomaly_calls_run_agent_with_ask_preset(monkeypatch):
    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["endpoint"] = endpoint
        return "diagnostic factice"

    monkeypatch.setattr(mcp_tools, "run_agent", fake_run_agent)

    result = await mcp_tools.explain_anomaly(question="Pic CPU hier ?")

    assert result["diagnosis"] == "diagnostic factice"
    assert captured == {"preset": "ask", "endpoint": "mcp/explain_anomaly"}


@pytest.mark.asyncio
async def test_register_tools_adds_all_four():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    mcp_tools.register_tools(server)

    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
