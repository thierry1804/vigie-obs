import pytest

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.options import (
    ASK_SYSTEM_PROMPT,
    BUSINESS_ANALYST_SYSTEM_PROMPT,
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


def test_build_ask_options_root_agent_disallows_direct_tool_access():
    options = build_ask_options("acme")
    assert set(options.disallowed_tools) == {
        "mcp__vigie-obs__query_loki",
        "mcp__vigie-obs__query_prometheus",
        "mcp__vigie-obs__query_traces",
        "mcp__vigie-biz__query_business_kpis",
        "mcp__vigie-biz__query_taxonomy",
    }


def test_build_ask_options_defines_diagnostic_investigator_subagent():
    options = build_ask_options("acme")
    assert set(options.agents) == {"diagnostic-investigator", "business-analyst"}
    diag = options.agents["diagnostic-investigator"]
    assert set(diag.tools) == {
        "mcp__vigie-obs__query_loki",
        "mcp__vigie-obs__query_prometheus",
        "mcp__vigie-obs__query_traces",
    }
    assert diag.maxTurns == MAX_TOOL_TURNS


def test_build_ask_options_defines_business_analyst_subagent():
    options = build_ask_options("acme")
    biz = options.agents["business-analyst"]
    assert set(biz.tools) == {
        "mcp__vigie-biz__query_business_kpis",
        "mcp__vigie-biz__query_taxonomy",
    }
    assert biz.model == MODEL_TRIAGE
    assert biz.prompt == BUSINESS_ANALYST_SYSTEM_PROMPT


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
