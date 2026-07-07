from datetime import datetime

from fastapi import APIRouter, Depends

from agent.middleware.tenant import get_tenant_id
from agent.services.agent_loop import agent_loop
from agent.services.taxonomy import load_taxonomy

router = APIRouter(tags=["report"])


@router.get("/report/daily")
async def daily_report(tenant_id: str = Depends(get_tenant_id)):
    taxonomy = load_taxonomy(tenant_id)
    kpi_hint = ""
    if taxonomy:
        names = [e["name"] for e in taxonomy.get("events", [])]
        kpi_hint = f" KPIs métier attendus: {', '.join(names)}."

    prompt = (
        "Génère le rapport quotidien pour les dernières 24h. "
        "Structure: 1) Santé technique, 2) Activité métier (stream_type=business, "
        f"business_event_type), 3) Points d'attention.{kpi_hint} Sois factuel."
    )
    report = await agent_loop(prompt, tenant_id=tenant_id, endpoint="report/daily")
    return {"date": datetime.now().date().isoformat(), "report": report, "tenant_id": tenant_id}
