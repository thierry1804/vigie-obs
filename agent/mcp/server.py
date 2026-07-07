"""Serveur MCP — outils consommables par agents externes."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from agent.db.models import Anomaly, Tenant
from agent.db.session import get_session
from agent.services.agent_loop import agent_loop
from agent.services.taxonomy import load_taxonomy
from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus

router = APIRouter(prefix="/mcp", tags=["mcp"])


def verify_mcp_token(
    authorization: str | None = Header(None),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token requis")
    token = authorization[7:]
    with get_session() as session:
        tenant = session.query(Tenant).filter(Tenant.mcp_token == token).first()
        if not tenant:
            raise HTTPException(status_code=403, detail="Token MCP invalide")
        if x_tenant_id and x_tenant_id != tenant.id:
            raise HTTPException(status_code=403, detail="Tenant non autorisé pour ce token")
        return tenant.id


class HealthParams(BaseModel):
    hours: float = 24


@router.post("/tools/get_project_health")
async def get_project_health(params: HealthParams, tenant_id: str = Depends(verify_mcp_token)):
    errors = await run_query_loki('{level="error"}', hours_back=params.hours, limit=20, tenant_id=tenant_id)
    cpu = await run_query_prometheus(
        '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    )
    taxonomy = load_taxonomy(tenant_id)
    business_types = [e["name"] for e in (taxonomy or {}).get("events", [])]
    return {
        "tenant_id": tenant_id,
        "window_hours": params.hours,
        "technical": {"errors_sample": errors[:500], "cpu_query": cpu[:300]},
        "business": {"event_types": business_types},
        "status": "degraded" if "error" in errors.lower()[:100] else "ok",
    }


class IncidentsParams(BaseModel):
    hours: float = 168
    status: str | None = None


@router.post("/tools/query_incidents")
async def query_incidents(params: IncidentsParams, tenant_id: str = Depends(verify_mcp_token)):
    since = datetime.utcnow() - timedelta(hours=params.hours)
    with get_session() as session:
        q = session.query(Anomaly).filter(
            Anomaly.tenant_id == tenant_id,
            Anomaly.created_at >= since,
        )
        if params.status:
            q = q.filter(Anomaly.status == params.status)
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


class KpiParams(BaseModel):
    hours: float = 24


@router.post("/tools/get_business_kpis")
async def get_business_kpis(params: KpiParams, tenant_id: str = Depends(verify_mcp_token)):
    taxonomy = load_taxonomy(tenant_id)
    kpis = {}
    if taxonomy:
        for ev in taxonomy.get("events", []):
            name = ev["name"]
            result = await run_query_loki(
                f'{{business_event_type="{name}"}}',
                hours_back=params.hours,
                limit=5,
                tenant_id=tenant_id,
            )
            count = max(0, result.count("\n") + (1 if result and "Aucun" not in result else 0))
            kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
    return {"tenant_id": tenant_id, "window_hours": params.hours, "kpis": kpis}


class ExplainParams(BaseModel):
    anomaly_id: int | None = None
    question: str | None = None


@router.post("/tools/explain_anomaly")
async def explain_anomaly(params: ExplainParams, tenant_id: str = Depends(verify_mcp_token)):
    context = params.question or ""
    if params.anomaly_id:
        with get_session() as session:
            a = session.get(Anomaly, params.anomaly_id)
            if a and a.tenant_id == tenant_id:
                context = f"Anomalie {a.title}: {a.diagnosis}"
    if not context:
        context = "Explique l'état de santé actuel du projet."
    diagnosis = await agent_loop(
        f"Investigation structurée (FAITS/HYPOTHÈSES):\n{context}",
        tenant_id=tenant_id,
        endpoint="mcp/explain_anomaly",
    )
    return {"tenant_id": tenant_id, "diagnosis": diagnosis}


@router.get("/sse")
async def mcp_sse(tenant_id: str = Depends(verify_mcp_token)):
    """Endpoint SSE — annonce des outils disponibles."""

    async def event_stream():
        tools = [
            "get_project_health",
            "query_incidents",
            "get_business_kpis",
            "explain_anomaly",
        ]
        yield f"data: {json.dumps({'type': 'tools/list', 'tools': tools, 'tenant_id': tenant_id})}\n\n"

    from fastapi.responses import StreamingResponse

    return StreamingResponse(event_stream(), media_type="text/event-stream")
