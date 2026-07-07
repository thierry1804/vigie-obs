"""Presets ClaudeAgentOptions par agent spécialisé VIGIE."""

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC
from agent.harness.hooks import make_audit_hook, make_budget_guard_hook, make_tenant_scope_hook
from agent.tools.mcp_server import build_obs_mcp_server

DIAGNOSTIC_SYSTEM_PROMPT = """Tu es VIGIE, un agent d'observabilité branché sur un projet en production.
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

# Convention de nommage confirmée par le spike (Task 2) : mcp__<serveur>__<outil>
_OBS_TOOL_MATCHER = "mcp__vigie-obs__.*"


def build_diagnostic_options(
    tenant_id: str, system_prompt: str | None = None
) -> ClaudeAgentOptions:
    """Preset de l'agent diagnostic (PEV) — outils Loki/Prometheus/Tempo, garde-fous par tenant."""
    return ClaudeAgentOptions(
        model=MODEL_DIAGNOSTIC,
        system_prompt=system_prompt or DIAGNOSTIC_SYSTEM_PROMPT,
        mcp_servers={"vigie-obs": build_obs_mcp_server(tenant_id)},
        max_turns=MAX_TOOL_TURNS,
        # Service headless : pas d'opérateur humain pour répondre aux invites
        # interactives du CLI. Le contrôle d'accès réel est assuré par les
        # hooks ci-dessous (budget, scoping tenant), pas par ce mode.
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_budget_guard_hook(tenant_id), make_tenant_scope_hook(tenant_id)],
                )
            ],
            "PostToolUse": [
                HookMatcher(matcher=_OBS_TOOL_MATCHER, hooks=[make_audit_hook(tenant_id)])
            ],
        },
    )
