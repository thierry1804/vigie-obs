"""Fabriques de hooks SDK — garde-fous appliqués à chaque appel outil."""

import re
from typing import Any

from agent.services.audit import audit
from agent.services.tokens import check_budget

HookCallback = Any  # claude_agent_sdk.types.HookCallback — alias pour lisibilité


def make_budget_guard_hook(tenant_id: str) -> HookCallback:
    """PreToolUse : refuse l'appel outil si le budget LLM du tenant est épuisé."""

    async def _budget_guard_hook(input_data, tool_use_id, context):
        ok, msg = check_budget(tenant_id)
        if not ok:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": msg,
                }
            }
        return {}

    return _budget_guard_hook


def make_tenant_scope_hook(tenant_id: str) -> HookCallback:
    """PreToolUse : verrouille toute requête LogQL sur le tenant courant.

    PromQL n'est pas concerné : Prometheus n'expose ici que des métriques hôte
    globales (CPU, disque, ...) sans notion de tenant à faire respecter.
    """

    tenant_pattern = re.compile(r'tenant_id\s*=\s*"([^"]+)"')

    async def _tenant_scope_hook(input_data, tool_use_id, context):
        tool_input = input_data.get("tool_input", {})
        logql = tool_input.get("logql")
        if logql is None:
            return {}

        match = tenant_pattern.search(logql)
        if match and match.group(1) != tenant_id:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Requête référence un tenant non autorisé: {match.group(1)}"
                    ),
                }
            }

        if match is None:
            # Aucun tenant_id explicite : au lieu de refuser (ce qui rendrait
            # l'outil inutilisable dès que le modèle oublie le filtre), on
            # injecte le scope tenant correct de façon transparente. Ferme la
            # fuite laissée ouverte par agent/tools/loki.py, qui ne scope pas
            # les requêtes commençant déjà par "{" (ex: '{level="error"}').
            scoped_logql = (
                logql[:1] + f'tenant_id="{tenant_id}",' + logql[1:]
                if logql.startswith("{")
                else f'{{tenant_id="{tenant_id}"}} ' + logql
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {**tool_input, "logql": scoped_logql},
                }
            }

        return {}

    return _tenant_scope_hook


def make_audit_hook(tenant_id: str) -> HookCallback:
    """PostToolUse : journalise chaque appel outil dans la table audit_logs."""

    async def _audit_hook(input_data, tool_use_id, context):
        audit(
            tenant_id,
            "tool_call",
            {"tool": input_data.get("tool_name"), "input": input_data.get("tool_input")},
        )
        return {}

    return _audit_hook
