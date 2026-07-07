"""Compteur tokens et budget LLM par tenant."""

from agent.config import DEFAULT_TENANT_ID
from agent.db.models import Tenant, TokenUsage
from agent.db.session import get_session


def record_usage(
    tenant_id: str,
    endpoint: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    with get_session() as session:
        session.add(
            TokenUsage(
                tenant_id=tenant_id,
                endpoint=endpoint,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        tenant = session.get(Tenant, tenant_id)
        if tenant:
            tenant.tokens_used += input_tokens + output_tokens
        session.commit()


def check_budget(tenant_id: str) -> tuple[bool, str]:
    with get_session() as session:
        tenant = session.get(Tenant, tenant_id or DEFAULT_TENANT_ID)
        if not tenant:
            return True, ""
        if tenant.tokens_used >= tenant.budget_llm_tokens:
            return False, f"Budget LLM épuisé ({tenant.tokens_used}/{tenant.budget_llm_tokens} tokens)."
        return True, ""


def get_usage_summary(tenant_id: str | None = None) -> dict:
    with get_session() as session:
        q = session.query(TokenUsage)
        if tenant_id:
            q = q.filter(TokenUsage.tenant_id == tenant_id)
        rows = q.order_by(TokenUsage.created_at.desc()).limit(100).all()
        total_in = sum(r.input_tokens for r in rows)
        total_out = sum(r.output_tokens for r in rows)
        tenant = session.get(Tenant, tenant_id or DEFAULT_TENANT_ID)
        return {
            "tenant_id": tenant_id or DEFAULT_TENANT_ID,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "budget_limit": tenant.budget_llm_tokens if tenant else 0,
            "tokens_used": tenant.tokens_used if tenant else 0,
            "recent_calls": len(rows),
        }
