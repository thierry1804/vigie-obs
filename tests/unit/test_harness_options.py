from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.options import (
    DIAGNOSTIC_SYSTEM_PROMPT,
    TAXONOMY_SYSTEM_PROMPT,
    TRIAGE_PROMPT,
    build_diagnostic_options,
    build_taxonomy_options,
    build_triage_options,
)


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
    post_hooks = options.hooks["PostToolUse"][0].hooks
    assert len(pre_hooks) == 2
    assert len(post_hooks) == 2  # audit + anonymize


def test_build_diagnostic_options_bypasses_interactive_permissions():
    options = build_diagnostic_options("acme")
    assert options.permission_mode == "bypassPermissions"


def test_build_triage_options_defaults():
    options = build_triage_options("acme")
    assert options.model == MODEL_TRIAGE
    assert options.max_turns == 1
    assert options.system_prompt == TRIAGE_PROMPT
    assert not options.mcp_servers


def test_build_triage_options_custom_system_prompt():
    options = build_triage_options("acme", system_prompt="Prompt de test")
    assert options.system_prompt == "Prompt de test"


def test_build_taxonomy_options_defaults():
    options = build_taxonomy_options("acme")
    assert options.model == MODEL_DIAGNOSTIC
    assert options.max_turns == 3
    assert options.system_prompt == TAXONOMY_SYSTEM_PROMPT
    assert "vigie-obs" in options.mcp_servers
    assert options.permission_mode == "bypassPermissions"


def test_build_taxonomy_options_has_all_four_hooks():
    options = build_taxonomy_options("acme")
    pre_hooks = options.hooks["PreToolUse"][0].hooks
    post_hooks = options.hooks["PostToolUse"][0].hooks
    assert len(pre_hooks) == 2  # budget + tenant_scope
    assert len(post_hooks) == 2  # audit + anonymize
