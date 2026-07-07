from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from agent.db.models import AlertHistory, AlertRule, Anomaly
from agent.db.session import get_session
from agent.middleware.tenant import get_tenant_id

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertRuleIn(BaseModel):
    name: str
    rule_type: str
    query: str
    threshold: float = 0.0
    enabled: bool = True
    cooldown_minutes: int = 60


class AlertConfigIn(BaseModel):
    rules: list[AlertRuleIn]
    slack_webhook: str | None = None


@router.get("/config")
async def get_alert_config(tenant_id: str = Depends(get_tenant_id)):
    with get_session() as session:
        rules = session.query(AlertRule).filter(AlertRule.tenant_id == tenant_id).all()
        return {
            "tenant_id": tenant_id,
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "rule_type": r.rule_type,
                    "query": r.query,
                    "threshold": r.threshold,
                    "enabled": r.enabled,
                    "cooldown_minutes": r.cooldown_minutes,
                }
                for r in rules
            ],
        }


@router.post("/config")
async def set_alert_config(body: AlertConfigIn, tenant_id: str = Depends(get_tenant_id)):
    with get_session() as session:
        session.query(AlertRule).filter(AlertRule.tenant_id == tenant_id).delete()
        for r in body.rules:
            session.add(
                AlertRule(
                    tenant_id=tenant_id,
                    name=r.name,
                    rule_type=r.rule_type,
                    query=r.query,
                    threshold=r.threshold,
                    enabled=r.enabled,
                    cooldown_minutes=r.cooldown_minutes,
                )
            )
        session.commit()
    return {"status": "ok", "rules_count": len(body.rules)}


@router.get("/history")
async def alert_history(
    tenant_id: str = Depends(get_tenant_id),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    with get_session() as session:
        q = session.query(Anomaly).filter(Anomaly.tenant_id == tenant_id)
        if status:
            q = q.filter(Anomaly.status == status)
        total = q.count()
        rows = q.order_by(Anomaly.created_at.desc()).offset(offset).limit(limit).all()
        history = session.query(AlertHistory).filter(AlertHistory.tenant_id == tenant_id).count()
        return {
            "tenant_id": tenant_id,
            "total": total,
            "history_events": history,
            "anomalies": [
                {
                    "id": a.id,
                    "status": a.status,
                    "title": a.title,
                    "rule_name": a.rule_name,
                    "created_at": a.created_at.isoformat(),
                    "diagnosis": (a.diagnosis or "")[:500],
                }
                for a in rows
            ],
        }


class AnomalyStatusIn(BaseModel):
    status: str


@router.patch("/anomalies/{anomaly_id}")
async def update_anomaly_status(
    anomaly_id: int,
    body: AnomalyStatusIn,
    tenant_id: str = Depends(get_tenant_id),
):
    allowed = {"open", "investigating", "resolved", "ignored"}
    if body.status not in allowed:
        return {"error": f"Statut invalide. Autorisés: {allowed}"}
    with get_session() as session:
        anomaly = session.get(Anomaly, anomaly_id)
        if not anomaly or anomaly.tenant_id != tenant_id:
            return {"error": "Anomalie introuvable"}
        anomaly.status = body.status
        session.commit()
    return {"status": "ok", "anomaly_id": anomaly_id, "new_status": body.status}
