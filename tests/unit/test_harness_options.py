from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC
from agent.harness.options import DIAGNOSTIC_SYSTEM_PROMPT, build_diagnostic_options


def test_build_diagnostic_options_defaults():
    options = build_diagnostic_options("acme")
    assert options.model == MODEL_DIAGNOSTIC
    assert options.max_turns == MAX_TOOL_TURNS
    assert options.system_prompt == DIAGNOSTIC_SYSTEM_PROMPT
    assert "vigie-obs" in options.mcp_servers


def test_build_diagnostic_options_custom_system_prompt():
    options = build_diagnostic_options("acme", system_prompt="Prompt de test")
    assert options.system_prompt == "Prompt de test"


def test_build_diagnostic_options_has_pretooluse_and_posttooluse_hooks():
    options = build_diagnostic_options("acme")
    assert "PreToolUse" in options.hooks
    assert "PostToolUse" in options.hooks
    pre_hooks = options.hooks["PreToolUse"][0].hooks
    assert len(pre_hooks) == 2


def test_build_diagnostic_options_bypasses_interactive_permissions():
    # VIGIE est un service headless : aucune invite interactive n'a de sens.
    # Le contrôle d'accès réel est assuré par nos hooks (budget/tenant-scope),
    # pas par le système de permission interactif du CLI. Confirmé nécessaire
    # par le spike de la Task 2 (sans ce réglage, l'outil est bloqué par
    # défaut en session non-interactive et n'est jamais exécuté).
    options = build_diagnostic_options("acme")
    assert options.permission_mode == "bypassPermissions"
