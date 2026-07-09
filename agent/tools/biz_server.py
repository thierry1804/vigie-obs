"""Serveur MCP in-process (outils métier : KPIs, taxonomie) — isolé de vigie-obs
pour ne pas exposer ces outils aux presets diagnostic/taxonomy existants."""

import json
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from agent.services.taxonomy import load_taxonomy
from agent.tools.loki import run_query_loki


def build_biz_tools(tenant_id: str) -> list[SdkMcpTool[Any]]:
    """Construit les 2 outils métier (KPIs, taxonomie) liés à un tenant précis."""

    @tool(
        "query_business_kpis",
        "Compte les occurrences de chaque événement métier de la taxonomie active "
        "sur une fenêtre récente.",
        {
            "type": "object",
            "properties": {
                "hours_back": {"type": "number"},
            },
        },
    )
    async def query_business_kpis_tool(args: dict[str, Any]) -> dict[str, Any]:
        hours_back = args.get("hours_back", 24)
        taxonomy = load_taxonomy(tenant_id)
        kpis: dict[str, Any] = {}
        if taxonomy:
            for ev in taxonomy.get("events", []):
                name = ev["name"]
                result = await run_query_loki(
                    f'{{business_event_type="{name}"}}',
                    hours_back=hours_back,
                    limit=5,
                    tenant_id=tenant_id,
                )
                count = max(
                    0, result.count("\n") + (1 if result and "Aucun" not in result else 0)
                )
                kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
        text = json.dumps({"window_hours": hours_back, "kpis": kpis}, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "query_taxonomy",
        "Retourne la taxonomie d'événements métier active pour ce tenant.",
        {"type": "object", "properties": {}},
    )
    async def query_taxonomy_tool(args: dict[str, Any]) -> dict[str, Any]:
        taxonomy = load_taxonomy(tenant_id)
        if not taxonomy:
            text = "Aucune taxonomie active pour ce tenant."
        else:
            text = json.dumps(taxonomy, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}]}

    return [query_business_kpis_tool, query_taxonomy_tool]


def build_biz_mcp_server(tenant_id: str) -> McpSdkServerConfig:
    """Serveur MCP in-process (vigie-biz) prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-biz", tools=build_biz_tools(tenant_id))
