import re

import pytest

from agent.tools.mcp_server import build_obs_tools


def _tool_by_name(tenant_id, name):
    tools = build_obs_tools(tenant_id)
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_query_loki_tool_scopes_tenant(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={
            "data": {
                "result": [
                    {"stream": {"level": "error"}, "values": [["1700000000000000000", "timeout"]]}
                ]
            }
        },
    )
    tool = _tool_by_name("acme", "query_loki")
    result = await tool.handler({"logql": '{level="error"}'})
    text = result["content"][0]["text"]
    assert "timeout" in text


@pytest.mark.asyncio
async def test_query_prometheus_tool_optional_range(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": [{"value": [1, "42"]}]}},
    )
    tool = _tool_by_name("acme", "query_prometheus")
    result = await tool.handler({"promql": "up"})
    assert "42" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_traces_tool_no_traces(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://tempo:3200/api/search.*"),
        json={"traces": []},
    )
    tool = _tool_by_name("acme", "query_traces")
    result = await tool.handler({"service": "web"})
    assert "Aucune trace" in result["content"][0]["text"]


def test_query_loki_schema_requires_logql():
    tool = _tool_by_name("acme", "query_loki")
    assert tool.input_schema["required"] == ["logql"]


def test_query_prometheus_schema_requires_promql():
    tool = _tool_by_name("acme", "query_prometheus")
    assert tool.input_schema["required"] == ["promql"]


def test_query_traces_schema_has_no_required_fields():
    tool = _tool_by_name("acme", "query_traces")
    assert "required" not in tool.input_schema
