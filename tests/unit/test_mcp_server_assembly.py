import pytest
from starlette.applications import Starlette

from agent.mcp.server import build_mcp_server, mcp_app


def test_mcp_app_is_starlette_instance():
    assert isinstance(mcp_app, Starlette)


@pytest.mark.asyncio
async def test_build_mcp_server_registers_all_four_tools():
    server = build_mcp_server()
    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
