import re

import pytest

from agent.tools.loki import _scope_logql_to_tenant, run_query_loki
from agent.tools.prometheus import run_query_prometheus


def test_scope_logql_injects_tenant_into_stream_selector():
    # Faille historiquement laissée par le hook (requête commençant par "{") :
    # le scope tenant doit être injecté dans le sélecteur de flux existant.
    scoped = _scope_logql_to_tenant('{level="error"}', "acme")
    assert scoped == '{tenant_id="acme",level="error"}'


def test_scope_logql_prepends_selector_for_bare_expression():
    scoped = _scope_logql_to_tenant("|= \"boom\"", "acme")
    assert scoped == '{tenant_id="acme"} |= "boom"'


def test_scope_logql_allows_matching_tenant():
    q = '{tenant_id="acme",level="error"}'
    assert _scope_logql_to_tenant(q, "acme") == q


def test_scope_logql_rejects_foreign_tenant():
    with pytest.raises(PermissionError):
        _scope_logql_to_tenant('{tenant_id="beta"}', "acme")


@pytest.mark.asyncio
async def test_query_loki_refuses_cross_tenant_query():
    result = await run_query_loki('{tenant_id="beta"}', tenant_id="acme")
    assert result.startswith("Refusé")


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
