"""Serveur MCP in-process (outils observabilité) — remplace agent/tools/registry.py."""

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus
from agent.tools.traces import run_query_traces


def build_obs_tools(tenant_id: str) -> list[SdkMcpTool[Any]]:
    """Construit les 3 outils d'observabilité liés à un tenant précis."""

    @tool(
        "query_loki",
        "Interroge les logs centralisés via LogQL. Labels : tenant_id, level, "
        "stream_type, business_event_type, trace_id.",
        {
            "type": "object",
            "properties": {
                "logql": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": ["logql"],
        },
    )
    async def query_loki_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_loki(
            logql=args["logql"],
            hours_back=args.get("hours_back", 24),
            limit=args.get("limit", 100),
            tenant_id=tenant_id,
        )
        return {"content": [{"type": "text", "text": result}]}

    @tool(
        "query_prometheus",
        "Exécute une requête PromQL instantanée ou de plage.",
        {
            "type": "object",
            "properties": {
                "promql": {"type": "string"},
                "range_hours": {"type": "number"},
            },
            "required": ["promql"],
        },
    )
    async def query_prometheus_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_prometheus(
            promql=args["promql"],
            range_hours=args.get("range_hours"),
        )
        return {"content": [{"type": "text", "text": result}]}

    @tool(
        "query_traces",
        "Interroge Tempo pour traces distribuées (si SDK OTel actif).",
        {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string"},
                "service": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
        },
    )
    async def query_traces_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_traces(
            trace_id=args.get("trace_id"),
            service=args.get("service"),
            hours_back=args.get("hours_back", 24),
            limit=args.get("limit", 20),
            tenant_id=tenant_id,
        )
        return {"content": [{"type": "text", "text": result}]}

    return [query_loki_tool, query_prometheus_tool, query_traces_tool]


def build_obs_mcp_server(tenant_id: str) -> McpSdkServerConfig:
    """Serveur MCP in-process prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-obs", tools=build_obs_tools(tenant_id))
