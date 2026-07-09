import re

import pytest
import yaml

from agent.tools.biz_server import build_biz_tools


def _tool_by_name(tenant_id, name):
    tools = build_biz_tools(tenant_id)
    return next(t for t in tools if t.name == name)


def _write_taxonomy(monkeypatch, tmp_path, events):
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)
    (tmp_path / "acme.yaml").write_text(
        yaml.dump({"events": events}, allow_unicode=True), encoding="utf-8"
    )


def _clear_taxonomy(monkeypatch, tmp_path):
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)


@pytest.mark.asyncio
async def test_query_taxonomy_tool_returns_active_taxonomy(monkeypatch, tmp_path):
    _write_taxonomy(
        monkeypatch,
        tmp_path,
        [{"name": "order_created", "patterns": ["commande créée"], "description": "Commande créée"}],
    )

    tool = _tool_by_name("acme", "query_taxonomy")
    result = await tool.handler({})

    assert "order_created" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_taxonomy_tool_no_taxonomy(monkeypatch, tmp_path):
    _clear_taxonomy(monkeypatch, tmp_path)

    tool = _tool_by_name("acme", "query_taxonomy")
    result = await tool.handler({})

    assert "Aucune taxonomie" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_business_kpis_tool_counts_events(monkeypatch, tmp_path, httpx_mock):
    _write_taxonomy(
        monkeypatch,
        tmp_path,
        [{"name": "order_created", "patterns": ["x"], "description": "Commande créée"}],
    )
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={
            "data": {
                "result": [
                    {"stream": {}, "values": [["1700000000000000000", "order created"]]}
                ]
            }
        },
    )

    tool = _tool_by_name("acme", "query_business_kpis")
    result = await tool.handler({"hours_back": 12})

    assert "order_created" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_business_kpis_tool_no_taxonomy_returns_empty_kpis(monkeypatch, tmp_path):
    _clear_taxonomy(monkeypatch, tmp_path)

    tool = _tool_by_name("acme", "query_business_kpis")
    result = await tool.handler({})

    assert '"kpis": {}' in result["content"][0]["text"]


def test_build_biz_tools_returns_two_tools():
    tools = build_biz_tools("acme")
    assert {t.name for t in tools} == {"query_business_kpis", "query_taxonomy"}


def test_query_business_kpis_schema_has_no_required_fields():
    tool = _tool_by_name("acme", "query_business_kpis")
    assert "required" not in tool.input_schema


def test_query_taxonomy_schema_has_no_properties():
    tool = _tool_by_name("acme", "query_taxonomy")
    assert tool.input_schema["properties"] == {}
