"""Logique des 4 outils MCP externes — indépendante du transport/serveur."""

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP

from agent.db.models import Anomaly
from agent.db.session import get_session
from agent.harness.runner import run_agent
from agent.services.taxonomy import load_taxonomy
from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus


def _current_tenant_id() -> str:
    access_token = get_access_token()
    return access_token.claims["tenant_id"]


async def get_project_health(hours: float = 24) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    errors = await run_query_loki(
        '{level="error"}', hours_back=hours, limit=20, tenant_id=tenant_id
    )
    cpu = await run_query_prometheus(
        '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    )
    taxonomy = load_taxonomy(tenant_id)
    business_types = [e["name"] for e in (taxonomy or {}).get("events", [])]
    return {
        "tenant_id": tenant_id,
        "window_hours": hours,
        "technical": {"errors_sample": errors[:500], "cpu_query": cpu[:300]},
        "business": {"event_types": business_types},
        "status": "degraded" if "error" in errors.lower()[:100] else "ok",
    }


async def query_incidents(hours: float = 168, status: str | None = None) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    since = datetime.now(UTC) - timedelta(hours=hours)
    with get_session() as session:
        q = session.query(Anomaly).filter(
            Anomaly.tenant_id == tenant_id, Anomaly.created_at >= since
        )
        if status:
            q = q.filter(Anomaly.status == status)
        rows = q.order_by(Anomaly.created_at.desc()).limit(50).all()
    return {
        "incidents": [
            {
                "id": r.id,
                "status": r.status,
                "title": r.title,
                "rule_name": r.rule_name,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


async def get_business_kpis(hours: float = 24) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    taxonomy = load_taxonomy(tenant_id)
    kpis: dict[str, Any] = {}
    if taxonomy:
        for ev in taxonomy.get("events", []):
            name = ev["name"]
            result = await run_query_loki(
                f'{{business_event_type="{name}"}}',
                hours_back=hours,
                limit=5,
                tenant_id=tenant_id,
            )
            count = max(
                0, result.count("\n") + (1 if result and "Aucun" not in result else 0)
            )
            kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
    return {"tenant_id": tenant_id, "window_hours": hours, "kpis": kpis}


async def explain_anomaly(
    anomaly_id: int | None = None, question: str | None = None
) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    context = question or ""
    if anomaly_id:
        with get_session() as session:
            a = session.get(Anomaly, anomaly_id)
            if a and a.tenant_id == tenant_id:
                context = f"Anomalie {a.title}: {a.diagnosis}"
    if not context:
        context = "Explique l'état de santé actuel du projet."
    diagnosis = await run_agent(
        "ask",
        f"Investigation structurée (FAITS/HYPOTHÈSES):\n{context}",
        tenant_id=tenant_id,
        endpoint="mcp/explain_anomaly",
    )
    return {"tenant_id": tenant_id, "diagnosis": diagnosis}


def register_tools(server: FastMCP) -> None:
    server.add_tool(get_project_health)
    server.add_tool(query_incidents)
    server.add_tool(get_business_kpis)
    server.add_tool(explain_anomaly)
