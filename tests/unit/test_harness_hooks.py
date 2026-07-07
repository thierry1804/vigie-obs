import pytest

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.harness.hooks import make_audit_hook, make_budget_guard_hook, make_tenant_scope_hook


def _pre_tool_input(tool_input: dict) -> dict:
    return {
        "session_id": "s1",
        "transcript_path": "/tmp/t",
        "cwd": "/app",
        "agent_id": "a1",
        "agent_type": "diagnostic",
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__vigie-obs__query_loki",
        "tool_input": tool_input,
        "tool_use_id": "tu1",
    }


@pytest.mark.asyncio
async def test_budget_guard_allows_when_budget_ok():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", budget_llm_tokens=1000, tokens_used=0))
        session.commit()
    hook = make_budget_guard_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output == {}


@pytest.mark.asyncio
async def test_budget_guard_denies_when_budget_exhausted():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", budget_llm_tokens=1000, tokens_used=1000))
        session.commit()
    hook = make_budget_guard_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_tenant_scope_allows_own_tenant():
    hook = make_tenant_scope_hook("acme")
    output = await hook(_pre_tool_input({"logql": '{tenant_id="acme",level="error"}'}), "tu1", {})
    assert output == {}


@pytest.mark.asyncio
async def test_tenant_scope_denies_other_tenant():
    hook = make_tenant_scope_hook("acme")
    output = await hook(_pre_tool_input({"logql": '{tenant_id="beta",level="error"}'}), "tu1", {})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "beta" in output["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_tenant_scope_denies_other_tenant_with_whitespace():
    hook = make_tenant_scope_hook("acme")
    output = await hook(_pre_tool_input({"logql": '{tenant_id = "beta",level="error"}'}), "tu1", {})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "beta" in output["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_audit_hook_writes_entry():
    from agent.db.models import AuditLog

    hook = make_audit_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output == {}
    with get_session() as session:
        rows = session.query(AuditLog).filter(AuditLog.tenant_id == "acme").all()
    assert any(r.action == "tool_call" for r in rows)
