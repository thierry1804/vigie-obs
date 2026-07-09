import pytest
from starlette.applications import Starlette

from agent.mcp.server import build_mcp_server


def test_build_mcp_server_returns_starlette_app():
    server = build_mcp_server()
    assert isinstance(server.streamable_http_app(), Starlette)


@pytest.mark.asyncio
async def test_build_mcp_server_registers_all_four_tools():
    server = build_mcp_server()
    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
