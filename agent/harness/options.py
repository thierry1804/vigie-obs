"""Presets ClaudeAgentOptions par agent spécialisé VIGIE."""

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.hooks import (
    anonymize_hook,
    make_audit_hook,
    make_budget_guard_hook,
    make_tenant_scope_hook,
)
from agent.tools.fs_scan_server import build_fs_scan_mcp_server
from agent.tools.mcp_server import build_obs_mcp_server
from discovery.scanner import DiscoveryReport

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

TRIAGE_PROMPT = """Qualifie cette alerte observabilité.
Réponds en JSON: {"is_anomaly": bool, "reason": "..."}
is_anomaly=false si bruit connu (healthcheck, redémarrage planifié, pic attendu)."""

TAXONOMY_SYSTEM_PROMPT = """Tu es un expert observabilité métier.
Ta tâche : proposer une taxonomie d'événements métier à partir des logs applicatifs.

Méthode :
1. Interroge l'outil query_loki (stream_type="business") pour échantillonner les logs métier
   sur la fenêtre demandée.
2. Si les résultats sont insuffisants ou ambigus, affine ta requête (autre fenêtre, autre filtre).
3. Propose une taxonomie YAML, format : events: [{name, patterns: [regex ou mots-clés], description}]

Règles :
- Ne conclus jamais sans avoir interrogé query_loki au moins une fois.
- Réponds uniquement en YAML valide, sans texte d'accompagnement."""

# Convention de nommage confirmée par le spike (Phase 1, Task 2) : mcp__<serveur>__<outil>
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
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_audit_hook(tenant_id), anonymize_hook],
                )
            ],
        },
    )


def build_triage_options(
    tenant_id: str, system_prompt: str | None = None
) -> ClaudeAgentOptions:
    """Preset de l'agent triage — classification single-shot bruit/anomalie, aucun outil."""
    return ClaudeAgentOptions(
        model=MODEL_TRIAGE,
        system_prompt=system_prompt or TRIAGE_PROMPT,
        max_turns=1,
    )


def build_taxonomy_options(
    tenant_id: str, system_prompt: str | None = None
) -> ClaudeAgentOptions:
    """Preset de l'agent taxonomie — explore query_loki lui-même, propose une taxonomie YAML."""
    return ClaudeAgentOptions(
        model=MODEL_DIAGNOSTIC,
        system_prompt=system_prompt or TAXONOMY_SYSTEM_PROMPT,
        mcp_servers={"vigie-obs": build_obs_mcp_server(tenant_id)},
        max_turns=3,
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_budget_guard_hook(tenant_id), make_tenant_scope_hook(tenant_id)],
                )
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_audit_hook(tenant_id), anonymize_hook],
                )
            ],
        },
    )


DISCOVERY_SYSTEM_PROMPT = """Tu es un expert observabilité chargé de classifier des sources de logs.
Pour chaque source déjà découverte, détermine son format/framework (ex: symfony, laravel, node, json, texte libre) à partir des échantillons fournis.

Méthode :
1. Examine les échantillons fournis pour chaque source.
2. Si un échantillon est trop court ou ambigu pour conclure, utilise l'outil sample_lines pour en obtenir davantage.
3. Pour CHAQUE source, appelle l'outil set_framework_hint avec ta conclusion — n'en oublie aucune.

Règles :
- Base-toi uniquement sur les échantillons observés, pas sur des suppositions.
- Réponds en français, de façon concise."""

_FS_TOOL_MATCHER = "mcp__vigie-fs__.*"


def build_discovery_options(
    tenant_id: str, system_prompt: str | None = None, *, report: DiscoveryReport
) -> ClaudeAgentOptions:
    """Preset de l'agent discovery — classifie les sources déjà scannées, rééchantillonne si besoin."""
    return ClaudeAgentOptions(
        model=MODEL_TRIAGE,
        system_prompt=system_prompt or DISCOVERY_SYSTEM_PROMPT,
        mcp_servers={"vigie-fs": build_fs_scan_mcp_server(report)},
        max_turns=6,
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(matcher=_FS_TOOL_MATCHER, hooks=[make_budget_guard_hook(tenant_id)])
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=_FS_TOOL_MATCHER,
                    hooks=[make_audit_hook(tenant_id), anonymize_hook],
                )
            ],
        },
    )


BUSINESS_ANALYST_SYSTEM_PROMPT = """Tu es un analyste métier VIGIE, sous-agent invoqué pour des
questions sur les événements métier.

Tu as accès à deux outils :
- query_taxonomy : retourne la taxonomie d'événements métier active (noms, descriptions, patterns).
- query_business_kpis : retourne un comptage d'occurrences par type d'événement métier sur une
  fenêtre récente.

Méthode :
1. Consulte query_taxonomy pour connaître les événements métier définis pour ce tenant.
2. Consulte query_business_kpis si la question porte sur des volumes ou des tendances.

Règles :
- Réponds en français, de façon concise et actionnable.
- Si aucune taxonomie n'existe pour ce tenant, dis-le clairement plutôt que d'inventer des
  événements."""

ASK_SYSTEM_PROMPT = """Tu es le routeur VIGIE. Tu ne réponds jamais directement à une question
toi-même.

Pour chaque question reçue, délègue-la via l'outil Agent à l'un des deux agents disponibles :
- diagnostic-investigator : questions techniques/infrastructure (erreurs, latence, disponibilité,
  logs, métriques, traces).
- business-analyst : questions métier (KPIs, événements métier, taxonomie).

Si la question mélange les deux registres, délègue d'abord à diagnostic-investigator, puis à
business-analyst si un éclairage métier est encore nécessaire, et synthétise les deux réponses.

Règle stricte : n'appelle jamais un outil toi-même, ton seul rôle est de choisir le ou les bons
agents et de leur transmettre la question."""

_BIZ_TOOL_MATCHER = "mcp__vigie-biz__.*"

_ASK_OBS_TOOL_NAMES = [
    "mcp__vigie-obs__query_loki",
    "mcp__vigie-obs__query_prometheus",
    "mcp__vigie-obs__query_traces",
]
_ASK_BIZ_TOOL_NAMES = [
    "mcp__vigie-biz__query_business_kpis",
    "mcp__vigie-biz__query_taxonomy",
]


def build_ask_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions:
    """Preset de l'agent orchestrateur ask — routeur pur, délègue toujours à un sous-agent."""
    # Late import to avoid circular dependency: taxonomy.py -> runner.py -> options.py
    from agent.tools.biz_server import build_biz_mcp_server

    return ClaudeAgentOptions(
        model=MODEL_DIAGNOSTIC,
        system_prompt=system_prompt or ASK_SYSTEM_PROMPT,
        mcp_servers={
            "vigie-obs": build_obs_mcp_server(tenant_id),
            "vigie-biz": build_biz_mcp_server(tenant_id),
        },
        # Agent racine : jamais d'appel direct, uniquement délégation via l'outil Agent.
        disallowed_tools=_ASK_OBS_TOOL_NAMES + _ASK_BIZ_TOOL_NAMES,
        agents={
            "diagnostic-investigator": AgentDefinition(
                description="Investigation technique (PEV) sur logs/métriques/traces.",
                prompt=DIAGNOSTIC_SYSTEM_PROMPT,
                tools=_ASK_OBS_TOOL_NAMES,
                maxTurns=MAX_TOOL_TURNS,
            ),
            "business-analyst": AgentDefinition(
                description="Analyse KPIs/taxonomie métier, léger, pas de boucle PEV.",
                prompt=BUSINESS_ANALYST_SYSTEM_PROMPT,
                tools=_ASK_BIZ_TOOL_NAMES,
                model=MODEL_TRIAGE,
                maxTurns=3,
            ),
        },
        max_turns=MAX_TOOL_TURNS,
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_budget_guard_hook(tenant_id), make_tenant_scope_hook(tenant_id)],
                ),
                HookMatcher(
                    matcher=_BIZ_TOOL_MATCHER,
                    hooks=[make_budget_guard_hook(tenant_id)],
                ),
            ],
            "PostToolUse": [
                HookMatcher(
                    matcher=_OBS_TOOL_MATCHER,
                    hooks=[make_audit_hook(tenant_id), anonymize_hook],
                ),
                HookMatcher(
                    matcher=_BIZ_TOOL_MATCHER,
                    hooks=[make_audit_hook(tenant_id), anonymize_hook],
                ),
            ],
        },
    )
