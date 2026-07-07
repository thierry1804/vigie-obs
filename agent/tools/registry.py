"""Registre des outils agent (tool use Anthropic)."""

from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus
from agent.tools.traces import run_query_traces

TOOLS = [
    {
        "name": "query_loki",
        "description": (
            "Interroge les logs centralisés via LogQL. Labels : tenant_id, level, "
            "stream_type, business_event_type, trace_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "logql": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": ["logql"],
        },
    },
    {
        "name": "query_prometheus",
        "description": "Exécute une requête PromQL instantanée ou de plage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "promql": {"type": "string"},
                "range_hours": {"type": "number"},
            },
            "required": ["promql"],
        },
    },
    {
        "name": "query_traces",
        "description": "Interroge Tempo pour traces distribuées (si SDK OTel actif).",
        "input_schema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string"},
                "service": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
        },
    },
]


async def dispatch_tool(name: str, args: dict, tenant_id: str | None = None) -> str:
    if name == "query_loki":
        return await run_query_loki(tenant_id=tenant_id, **args)
    if name == "query_prometheus":
        return await run_query_prometheus(**args)
    if name == "query_traces":
        return await run_query_traces(tenant_id=tenant_id, **args)
    return f"Outil inconnu : {name}"
