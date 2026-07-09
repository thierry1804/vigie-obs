"""Alerting proactif — seuils, triage, escalade, canaux sortants."""

import hashlib
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import httpx

from agent.config import SLACK_WEBHOOK_URL, SMTP_FROM, SMTP_HOST, SMTP_PORT, SMTP_TO
from agent.db.models import AlertHistory, AlertRule, Anomaly
from agent.db.session import get_session
from agent.services.agent_loop import agent_loop
from agent.services.audit import audit
from agent.services.triage import triage_alert
from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus

logger = logging.getLogger("vigie.alerting")


def _signature(tenant_id: str, rule_name: str, value: str) -> str:
    raw = f"{tenant_id}:{rule_name}:{value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _recent_alert(tenant_id: str, signature: str, cooldown_minutes: int) -> bool:
    since = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
    with get_session() as session:
        row = (
            session.query(Anomaly)
            .filter(
                Anomaly.tenant_id == tenant_id,
                Anomaly.signature == signature,
                Anomaly.created_at > since,
            )
            .first()
        )
        return row is not None


async def evaluate_rule(rule: AlertRule) -> tuple[bool, str]:
    if rule.rule_type == "logql":
        result = await run_query_loki(rule.query, hours_back=1, limit=10, tenant_id=rule.tenant_id)
        triggered = "Aucun résultat" not in result and len(result) > 20
        return triggered, result[:500]
    if rule.rule_type == "promql":
        result = await run_query_prometheus(rule.query)
        try:
            import json

            data = json.loads(result)
            values = data.get("result", [])
            for v in values:
                val = v.get("value", [None, "0"])[1]
                if float(val) >= rule.threshold:
                    return True, f"{rule.name}={val} (seuil {rule.threshold})"
        except (json.JSONDecodeError, ValueError, IndexError):
            pass
        return False, result[:200]
    return False, ""


async def send_slack(message: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.info("Slack mock: %s", message[:200])
        return True
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(SLACK_WEBHOOK_URL, json={"text": message})
        return r.status_code < 300


def send_email(message: str, subject: str = "VIGIE Alert") -> bool:
    if not SMTP_HOST or not SMTP_TO:
        logger.info("Email mock: %s", subject)
        return True
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM or "vigie@localhost"
    msg["To"] = SMTP_TO
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.send_message(msg)
    return True


async def process_alert(
    tenant_id: str,
    rule: AlertRule,
    context: str,
) -> Anomaly | None:
    sig = _signature(tenant_id, rule.name, context[:100])
    if _recent_alert(tenant_id, sig, rule.cooldown_minutes):
        return None

    is_anomaly, reason = await triage_alert(tenant_id, sig, context)
    if not is_anomaly:
        audit(tenant_id, "alert_suppressed", {"rule": rule.name, "reason": reason})
        return None

    prompt = (
        f"Alerte {rule.name} déclenchée.\nContexte:\n{context}\n\n"
        "Rédige un message d'alerte en langage naturel avec FAITS, HYPOTHÈSES et actions suggérées."
    )
    diagnosis = await agent_loop(prompt, tenant_id=tenant_id, endpoint="alert")

    with get_session() as session:
        anomaly = Anomaly(
            tenant_id=tenant_id,
            signature=sig,
            status="open",
            title=f"Alerte: {rule.name}",
            diagnosis=diagnosis,
            rule_name=rule.name,
        )
        session.add(anomaly)
        session.commit()
        session.refresh(anomaly)

    message = f"**VIGIE [{tenant_id}]** {rule.name}\n\n{diagnosis}"
    await send_slack(message)
    send_email(message)

    with get_session() as session:
        session.add(
            AlertHistory(
                tenant_id=tenant_id,
                anomaly_id=anomaly.id,
                channel="slack+email",
                message=message[:2000],
            )
        )
        session.commit()

    audit(tenant_id, "alert_sent", {"rule": rule.name, "anomaly_id": anomaly.id})
    return anomaly


async def run_alert_cycle() -> int:
    count = 0
    with get_session() as session:
        rules = session.query(AlertRule).filter(AlertRule.enabled.is_(True)).all()
        rules_data = [
            (r.id, r.tenant_id, r.name, r.rule_type, r.query, r.threshold, r.cooldown_minutes)
            for r in rules
        ]
    for _, tenant_id, name, rule_type, query, threshold, cooldown in rules_data:
        rule = AlertRule(
            tenant_id=tenant_id,
            name=name,
            rule_type=rule_type,
            query=query,
            threshold=threshold,
            cooldown_minutes=cooldown,
        )
        triggered, ctx = await evaluate_rule(rule)
        if triggered:
            result = await process_alert(tenant_id, rule, ctx)
            if result:
                count += 1
    return count
