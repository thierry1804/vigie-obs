"""Audit trail structuré par tenant."""

import json
import logging

from agent.db.models import AuditLog
from agent.db.session import get_session

logger = logging.getLogger("vigie.audit")


def audit(tenant_id: str, action: str, detail: str | dict | None = None) -> None:
    detail_str = json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else detail
    logger.info(json.dumps({"tenant_id": tenant_id, "action": action, "detail": detail_str}))
    with get_session() as session:
        session.add(AuditLog(tenant_id=tenant_id, action=action, detail=detail_str))
        session.commit()
