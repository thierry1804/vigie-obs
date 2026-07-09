"""Point d'entrée unique vers le LLM — harness Claude Agent SDK."""

from claude_agent_sdk import ResultMessage, query

from agent.config import MODEL_DIAGNOSTIC
from agent.harness.options import (
    build_ask_options,
    build_diagnostic_options,
    build_discovery_options,
    build_taxonomy_options,
    build_triage_options,
)
from agent.services.llm_client import _mock_enabled
from agent.services.tokens import check_budget, record_usage

_MOCK_ANSWERS = {
    "diagnostic": (
        "Réponse mock VIGIE. FAITS : données simulées. "
        "HYPOTHÈSES : aucune conclusion réelle sans API."
    ),
    "triage": '{"is_anomaly": true, "reason": "anomalie plausible (mock)"}',
    "taxonomy": (
        "events:\n"
        "  - name: order_created\n"
        "    patterns: ['commande créée', 'order created']\n"
        "    description: Commande créée (mock)\n"
    ),
    "discovery": "Classification terminée (mock).",
    "ask": "Réponse mock VIGIE (routeur). Délégation simulée (mock).",
}

_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
    "triage": build_triage_options,
    "taxonomy": build_taxonomy_options,
    "discovery": build_discovery_options,
    "ask": build_ask_options,
}


async def run_agent(
    preset: str,
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
    **preset_kwargs,
) -> str:
    """Exécute un agent (preset donné) via le harness, ou renvoie une réponse fixture en mode mock."""
    if _mock_enabled():
        return _MOCK_ANSWERS[preset]

    ok, msg = check_budget(tenant_id)
    if not ok:
        return msg

    options = _PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt, **preset_kwargs)

    result_message: ResultMessage | None = None
    try:
        async for message in query(prompt=user_message, options=options):
            if isinstance(message, ResultMessage):
                result_message = message
    except Exception as e:
        return f"Erreur harness agentique : {e}"

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

    if result_message.is_error:
        details = result_message.errors or result_message.result or "échec sans détail"
        return f"Erreur harness agentique : {details}"

    return result_message.result or ""
