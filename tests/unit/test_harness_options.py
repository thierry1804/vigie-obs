import pytest

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.options import (
    ASK_SYSTEM_PROMPT,
    DIAGNOSTIC_SYSTEM_PROMPT,
    DISCOVERY_SYSTEM_PROMPT,
    TAXONOMY_SYSTEM_PROMPT,
    TRIAGE_PROMPT,
    build_ask_options,
    build_diagnostic_options,
    build_discovery_options,
    build_taxonomy_options,
    build_triage_options,
)
from discovery.scanner import DiscoveryReport, LogSource


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


def _sample_report():
    return DiscoveryReport(
        target="/srv/app",
        log_sources=[LogSource(path="/srv/app/var/log", glob="/srv/app/var/log/*.log")],
    )


def test_build_discovery_options_defaults():
    options = build_discovery_options("acme", report=_sample_report())
    assert options.model == MODEL_TRIAGE
    assert options.max_turns == 6
    assert options.system_prompt == DISCOVERY_SYSTEM_PROMPT
    assert "vigie-fs" in options.mcp_servers
    assert options.permission_mode == "bypassPermissions"


def test_build_discovery_options_custom_system_prompt():
    options = build_discovery_options("acme", system_prompt="Prompt de test", report=_sample_report())
    assert options.system_prompt == "Prompt de test"


def test_build_discovery_options_has_budget_guard_only_on_pretooluse():
    options = build_discovery_options("acme", report=_sample_report())
    pre_hooks = options.hooks["PreToolUse"][0].hooks
    post_hooks = options.hooks["PostToolUse"][0].hooks
    assert len(pre_hooks) == 1  # budget uniquement, pas de tenant_scope
    assert len(post_hooks) == 2  # audit + anonymize


def test_build_discovery_options_requires_report():
    with pytest.raises(TypeError):
        build_discovery_options("acme")


def test_build_ask_options_defaults():
    options = build_ask_options("acme")
    assert options.model == MODEL_DIAGNOSTIC
    assert options.max_turns == MAX_TOOL_TURNS
    assert options.system_prompt == ASK_SYSTEM_PROMPT
    assert "vigie-obs" in options.mcp_servers
    assert "vigie-biz" in options.mcp_servers
    assert options.permission_mode == "bypassPermissions"


def test_build_ask_options_custom_system_prompt():
    options = build_ask_options("acme", system_prompt="Prompt de test")
    assert options.system_prompt == "Prompt de test"


def test_build_ask_options_is_flat_agent_without_subagents():
    # Sous-agent + hook PreToolUse + outil MCP réel = "Stream closed" intermittent
    # (4/6 runs réels), alors que le même hook sur un appel racine n'a jamais échoué
    # (0/4) — cf. docstring de build_ask_options. D'où un agent racine unique.
    options = build_ask_options("acme")
    assert not options.agents
    assert not options.disallowed_tools


def test_build_ask_options_has_hooks_for_both_mcp_matchers():
    options = build_ask_options("acme")
    pre_hooks = options.hooks["PreToolUse"]
    post_hooks = options.hooks["PostToolUse"]
    assert len(pre_hooks) == 2
    assert len(post_hooks) == 2
    obs_pre = next(h for h in pre_hooks if h.matcher == "mcp__vigie-obs__.*")
    biz_pre = next(h for h in pre_hooks if h.matcher == "mcp__vigie-biz__.*")
    assert len(obs_pre.hooks) == 2  # budget + tenant_scope
    assert len(biz_pre.hooks) == 1  # budget uniquement (pas de logql à scoper)
