"""Tests bout-en-bout du serveur MCP externe — auth + transport réels, vrai port TCP."""

import asyncio
import socket

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def mcp_base_url():
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_valid_token_lists_four_tools(mcp_base_url):
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", mcp_token="tok-acme"))
        session.commit()

    async with streamablehttp_client(
        f"{mcp_base_url}/mcp", headers={"Authorization": "Bearer tok-acme"}
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.list_tools()

    names = {t.name for t in result.tools}
    assert names == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }


@pytest.mark.asyncio
async def test_invalid_token_rejected(mcp_base_url):
    # Le SDK propage l'échec HTTP au travers d'un ExceptionGroup (anyio TaskGroup) ;
    # la forme imbriquée n'est pas garantie stable, donc on capture large mais on
    # vérifie qu'un httpx.HTTPStatusError 401 est bien présent dans la chaîne.
    with pytest.raises(Exception) as exc_info:
        async with streamablehttp_client(
            f"{mcp_base_url}/mcp", headers={"Authorization": "Bearer invalid-token"}
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as client:
                await client.initialize()

    def _flatten(exc):
        subs = getattr(exc, "exceptions", None)
        if subs is None:
            return [exc]
        flat = []
        for sub in subs:
            flat.extend(_flatten(sub))
        return flat

    causes = _flatten(exc_info.value)
    assert any(
        isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 401
        for e in causes
    )


@pytest.mark.asyncio
async def test_tool_call_scoped_to_tenant(mcp_base_url):
    with get_session() as session:
        session.add(Tenant(id="alpha", name="Alpha", mcp_token="tok-alpha"))
        session.add(Tenant(id="beta", name="Beta", mcp_token="tok-beta"))
        session.commit()

    async def _kpis_for(token):
        async with streamablehttp_client(
            f"{mcp_base_url}/mcp", headers={"Authorization": f"Bearer {token}"}
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await client.call_tool("get_business_kpis", {"hours": 24})

    result_alpha = await _kpis_for("tok-alpha")
    result_beta = await _kpis_for("tok-beta")

    assert result_alpha.structuredContent["tenant_id"] == "alpha"
    assert result_beta.structuredContent["tenant_id"] == "beta"
