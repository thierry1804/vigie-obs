import re

import pytest

from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus


@pytest.mark.asyncio
async def test_query_loki_success(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={
            "data": {
                "result": [
                    {
                        "stream": {"level": "error"},
                        "values": [["1700000000000000000", "timeout"]],
                    }
                ]
            }
        },
    )
    result = await run_query_loki('{level="error"}', tenant_id="default")
    assert "timeout" in result
    assert "error" in result


@pytest.mark.asyncio
async def test_query_prometheus_instant(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": [{"value": [1, "42"]}]}},
    )
    result = await run_query_prometheus("up")
    assert "42" in result
