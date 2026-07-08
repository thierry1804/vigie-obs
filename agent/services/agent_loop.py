"""Boucle agentique diagnostic — délègue au harness (Claude Agent SDK)."""

from agent.harness.runner import run_agent


async def agent_loop(
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
) -> str:
    return await run_agent(
        "diagnostic",
        user_message,
        tenant_id=tenant_id,
        endpoint=endpoint,
        system_prompt=system_prompt,
    )
