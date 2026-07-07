"""Point d'entrée unique vers le LLM pour l'agent diagnostic — harness Claude Agent SDK."""

from claude_agent_sdk import ResultMessage, query

from agent.config import MODEL_DIAGNOSTIC
from agent.harness.options import build_diagnostic_options
from agent.services.llm_client import _mock_enabled
from agent.services.tokens import record_usage

MOCK_DIAGNOSTIC_ANSWER = (
    "Réponse mock VIGIE. FAITS : données simulées. HYPOTHÈSES : aucune conclusion réelle sans API."
)

_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
}


async def run_agent(
    preset: str,
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
) -> str:
    """Exécute un agent (preset donné) via le harness, ou renvoie une réponse fixture en mode mock."""
    if _mock_enabled():
        return MOCK_DIAGNOSTIC_ANSWER

    options = _PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt)

    result_message: ResultMessage | None = None
    async for message in query(prompt=user_message, options=options):
        if isinstance(message, ResultMessage):
            result_message = message

    if result_message is None:
        return "Erreur : aucune réponse reçue du harness agentique."

    usage = result_message.usage or {}
    record_usage(
        tenant_id,
        endpoint,
        options.model or MODEL_DIAGNOSTIC,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    return result_message.result or ""
