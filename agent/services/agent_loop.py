"""Boucle agentique Plan-Exécute-Vérifie."""

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC
from agent.services.llm_client import create_message
from agent.services.tokens import check_budget, record_usage
from agent.tools.registry import TOOLS, dispatch_tool

SYSTEM_PROMPT = """Tu es VIGIE, un agent d'observabilité branché sur un projet en production.
Tu as accès aux logs (Loki/LogQL), aux métriques système (Prometheus/PromQL) et aux traces (Tempo).

Méthode de diagnostic (boucle Plan-Exécute-Vérifie) :
1. PLAN : formule une hypothèse et la ou les requêtes qui la testeraient.
2. EXÉCUTE : lance les requêtes nécessaires (commence large, affine ensuite).
3. VÉRIFIE : avant de conclure, challenge ta propre hypothèse.

Règles :
- Distingue toujours les FAITS (observés dans les données) des HYPOTHÈSES.
- Si les données sont insuffisantes, dis-le et propose une instrumentation complémentaire.
- Réponds en français, de façon concise et actionnable.
- Pour les événements métier, exploite stream_type="business" et business_event_type."""


async def agent_loop(
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
) -> str:
    ok, msg = check_budget(tenant_id)
    if not ok:
        return msg

    messages = [{"role": "user", "content": user_message}]
    system = system_prompt or SYSTEM_PROMPT

    for _ in range(MAX_TOOL_TURNS):
        response = create_message(
            model=MODEL_DIAGNOSTIC,
            max_tokens=2000,
            system=system,
            tools=TOOLS,
            messages=messages,
        )
        record_usage(
            tenant_id,
            endpoint,
            MODEL_DIAGNOSTIC,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await dispatch_tool(block.name, block.input, tenant_id=tenant_id)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        messages.append({"role": "user", "content": tool_results})

    return "Budget d'investigation épuisé sans conclusion ferme — affinez la question."
