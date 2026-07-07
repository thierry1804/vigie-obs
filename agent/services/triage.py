"""Triage Haiku — qualification bruit vs anomalie."""

import json
from datetime import datetime, timedelta, timezone

from agent.config import MODEL_TRIAGE
from agent.db.models import TriageCache
from agent.db.session import get_session
from agent.services.llm_client import create_message
from agent.services.tokens import record_usage

TRIAGE_PROMPT = """Qualifie cette alerte observabilité.
Réponds en JSON: {"is_anomaly": bool, "reason": "..."}
is_anomaly=false si bruit connu (healthcheck, redémarrage planifié, pic attendu)."""


def _cache_get(tenant_id: str, signature: str) -> bool | None:
    with get_session() as session:
        row = (
            session.query(TriageCache)
            .filter(
                TriageCache.tenant_id == tenant_id,
                TriageCache.signature == signature,
                TriageCache.expires_at > datetime.now(timezone.utc),
            )
            .first()
        )
        if row:
            return not row.is_noise
        return None


def _cache_set(tenant_id: str, signature: str, is_noise: bool, hours: int = 24) -> None:
    with get_session() as session:
        session.add(
            TriageCache(
                tenant_id=tenant_id,
                signature=signature,
                is_noise=is_noise,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            )
        )
        session.commit()


def triage_alert(tenant_id: str, signature: str, context: str) -> tuple[bool, str]:
    cached = _cache_get(tenant_id, signature)
    if cached is not None:
        return cached, "cache"

    response = create_message(
        model=MODEL_TRIAGE,
        max_tokens=300,
        system=TRIAGE_PROMPT,
        messages=[{"role": "user", "content": context}],
    )
    record_usage(tenant_id, "triage", MODEL_TRIAGE, response.usage.input_tokens, response.usage.output_tokens)
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        data = json.loads(text.strip().strip("`").replace("json", ""))
        is_anomaly = bool(data.get("is_anomaly", True))
        reason = data.get("reason", "")
    except (json.JSONDecodeError, TypeError):
        is_anomaly = "false" not in text.lower()
        reason = text[:200]
    _cache_set(tenant_id, signature, is_noise=not is_anomaly)
    return is_anomaly, reason
