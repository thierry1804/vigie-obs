# Harness Phase 3 — agent orchestrateur `ask` : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un preset `"ask"` au harness — un agent racine qui ne répond jamais lui-même mais délègue systématiquement à l'un de deux sous-agents (`diagnostic-investigator` pour Loki/Prometheus/Tempo, `business-analyst` pour KPIs/taxonomie métier) — et brancher `/ask` + `mcp/explain_anomaly` dessus à la place du preset `diagnostic` figé.

**Architecture:** Nouveau serveur MCP in-process `vigie-biz` (2 outils métier, isolé de `vigie-obs` pour ne pas affecter les presets diagnostic/taxonomy existants) ; nouveau builder `build_ask_options()` dans `agent/harness/options.py` qui peuple `ClaudeAgentOptions.agents` avec deux `AgentDefinition` et bloque l'accès direct aux outils MCP côté agent racine via `disallowed_tools` ; le dict `_PRESET_BUILDERS` de `agent/harness/runner.py` gagne une entrée `"ask"` ; deux appelants (`agent/routes/ask.py`, `agent/mcp/server.py::explain_anomaly`) basculent de `agent_loop()` vers `run_agent("ask", ...)`.

**Tech Stack:** Python 3.12+, `claude-agent-sdk` (déjà installé, `AgentDefinition`/`ClaudeAgentOptions.agents`/`ClaudeAgentOptions.disallowed_tools` utilisés pour la première fois dans ce projet), FastAPI, pytest (`asyncio_mode = "auto"`), `pytest-httpx` pour mocker Loki.

## Global Constraints

- Tout preset exposant des outils doit utiliser `permission_mode="bypassPermissions"` — sans ça, un service headless bloque l'appel d'outil par défaut (confirmé Phase 1, `docs/superpowers/harness-migration-status.md` §4.2).
- Convention de nommage des outils MCP vus par le modèle : `mcp__<nom_serveur>__<nom_outil>` (§4.3 du doc de suivi) — c'est ce nom complet qu'attendent `AgentDefinition.tools` et `ClaudeAgentOptions.disallowed_tools`.
- `PostToolUse` `tool_response`/`updatedMCPToolOutput` sont toujours une liste de blocs `{"type": "text", "text": "..."}`, jamais un dict `{"content": [...]}` (§4.4) — déjà géré par les hooks existants (`make_audit_hook`, `anonymize_hook`), rien à changer ici, juste à ne pas casser en réutilisant les mêmes hooks tels quels.
- `run_agent()` (`agent/harness/runner.py`) reste le point d'entrée unique vers le LLM et gère déjà budget/erreurs/usage de façon générique par preset — aucune de ces tâches ne doit dupliquer cette logique, seulement ajouter une entrée `"ask"` aux dicts de dispatch.
- Mécanisme SDK neuf pour ce projet (`disallowed_tools` racine + `agents=` avec `AgentDefinition.tools` par sous-agent) : la Task 2 doit vérifier par un test réel (voir Step de vérification dédié) que les deux listes sont bien indépendantes plutôt que de le supposer — cohérent avec la pratique du projet de ne jamais documenter un fait SDK non vérifié (§4 du doc de suivi).

---

### Task 1 : Serveur MCP business `vigie-biz`

**Files:**
- Create: `agent/tools/biz_server.py`
- Test: `tests/unit/test_biz_server.py`

**Interfaces:**
- Consumes : `agent.services.taxonomy.load_taxonomy(tenant_id: str) -> dict | None` (existant, `agent/services/taxonomy.py:88`) ; `agent.tools.loki.run_query_loki(logql: str, hours_back: float = 24, limit: int = 100, tenant_id: str | None = None) -> str` (existant, `agent/tools/loki.py:11`).
- Produces : `build_biz_tools(tenant_id: str) -> list[SdkMcpTool[Any]]` (2 outils : `query_business_kpis`, `query_taxonomy`) ; `build_biz_mcp_server(tenant_id: str) -> McpSdkServerConfig` — consommés par la Task 2.

- [ ] **Step 1: Écrire les tests (échouent, le module n'existe pas encore)**

Créer `tests/unit/test_biz_server.py` :

```python
import re

import pytest
import yaml

from agent.tools.biz_server import build_biz_tools


def _tool_by_name(tenant_id, name):
    tools = build_biz_tools(tenant_id)
    return next(t for t in tools if t.name == name)


def _write_taxonomy(monkeypatch, tmp_path, events):
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)
    (tmp_path / "acme.yaml").write_text(
        yaml.dump({"events": events}, allow_unicode=True), encoding="utf-8"
    )


def _clear_taxonomy(monkeypatch, tmp_path):
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)


@pytest.mark.asyncio
async def test_query_taxonomy_tool_returns_active_taxonomy(monkeypatch, tmp_path):
    _write_taxonomy(
        monkeypatch,
        tmp_path,
        [{"name": "order_created", "patterns": ["commande créée"], "description": "Commande créée"}],
    )

    tool = _tool_by_name("acme", "query_taxonomy")
    result = await tool.handler({})

    assert "order_created" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_taxonomy_tool_no_taxonomy(monkeypatch, tmp_path):
    _clear_taxonomy(monkeypatch, tmp_path)

    tool = _tool_by_name("acme", "query_taxonomy")
    result = await tool.handler({})

    assert "Aucune taxonomie" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_business_kpis_tool_counts_events(monkeypatch, tmp_path, httpx_mock):
    _write_taxonomy(
        monkeypatch,
        tmp_path,
        [{"name": "order_created", "patterns": ["x"], "description": "Commande créée"}],
    )
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={
            "data": {
                "result": [
                    {"stream": {}, "values": [["1700000000000000000", "order created"]]}
                ]
            }
        },
    )

    tool = _tool_by_name("acme", "query_business_kpis")
    result = await tool.handler({"hours_back": 12})

    assert "order_created" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_business_kpis_tool_no_taxonomy_returns_empty_kpis(monkeypatch, tmp_path):
    _clear_taxonomy(monkeypatch, tmp_path)

    tool = _tool_by_name("acme", "query_business_kpis")
    result = await tool.handler({})

    assert '"kpis": {}' in result["content"][0]["text"]


def test_build_biz_tools_returns_two_tools():
    tools = build_biz_tools("acme")
    assert {t.name for t in tools} == {"query_business_kpis", "query_taxonomy"}


def test_query_business_kpis_schema_has_no_required_fields():
    tool = _tool_by_name("acme", "query_business_kpis")
    assert "required" not in tool.input_schema


def test_query_taxonomy_schema_has_no_properties():
    tool = _tool_by_name("acme", "query_taxonomy")
    assert tool.input_schema["properties"] == {}
```

- [ ] **Step 2: Lancer les tests, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_biz_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.tools.biz_server'`

- [ ] **Step 3: Implémenter `agent/tools/biz_server.py`**

```python
"""Serveur MCP in-process (outils métier : KPIs, taxonomie) — isolé de vigie-obs
pour ne pas exposer ces outils aux presets diagnostic/taxonomy existants."""

import json
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from agent.services.taxonomy import load_taxonomy
from agent.tools.loki import run_query_loki


def build_biz_tools(tenant_id: str) -> list[SdkMcpTool[Any]]:
    """Construit les 2 outils métier (KPIs, taxonomie) liés à un tenant précis."""

    @tool(
        "query_business_kpis",
        "Compte les occurrences de chaque événement métier de la taxonomie active "
        "sur une fenêtre récente.",
        {
            "type": "object",
            "properties": {
                "hours_back": {"type": "number"},
            },
        },
    )
    async def query_business_kpis_tool(args: dict[str, Any]) -> dict[str, Any]:
        hours_back = args.get("hours_back", 24)
        taxonomy = load_taxonomy(tenant_id)
        kpis: dict[str, Any] = {}
        if taxonomy:
            for ev in taxonomy.get("events", []):
                name = ev["name"]
                result = await run_query_loki(
                    f'{{business_event_type="{name}"}}',
                    hours_back=hours_back,
                    limit=5,
                    tenant_id=tenant_id,
                )
                count = max(
                    0, result.count("\n") + (1 if result and "Aucun" not in result else 0)
                )
                kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
        text = json.dumps({"window_hours": hours_back, "kpis": kpis}, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "query_taxonomy",
        "Retourne la taxonomie d'événements métier active pour ce tenant.",
        {"type": "object", "properties": {}},
    )
    async def query_taxonomy_tool(args: dict[str, Any]) -> dict[str, Any]:
        taxonomy = load_taxonomy(tenant_id)
        if not taxonomy:
            text = "Aucune taxonomie active pour ce tenant."
        else:
            text = json.dumps(taxonomy, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}]}

    return [query_business_kpis_tool, query_taxonomy_tool]


def build_biz_mcp_server(tenant_id: str) -> McpSdkServerConfig:
    """Serveur MCP in-process (vigie-biz) prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-biz", tools=build_biz_tools(tenant_id))
```

- [ ] **Step 4: Lancer les tests, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_biz_server.py -v`
Expected: PASS — 8 tests verts.

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/tools/biz_server.py tests/unit/test_biz_server.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/tools/biz_server.py tests/unit/test_biz_server.py
git commit -m "feat: serveur MCP vigie-biz (query_business_kpis, query_taxonomy)"
```

---

### Task 2 : Preset `build_ask_options` + sous-agents

**Files:**
- Modify: `agent/harness/options.py`
- Test: `tests/unit/test_harness_options.py`

**Interfaces:**
- Consumes : `build_biz_mcp_server(tenant_id: str) -> McpSdkServerConfig` (Task 1) ; `build_obs_mcp_server(tenant_id: str) -> McpSdkServerConfig` (existant, `agent/tools/mcp_server.py:83`) ; `DIAGNOSTIC_SYSTEM_PROMPT` (existant, `agent/harness/options.py:16`) ; `make_budget_guard_hook`/`make_tenant_scope_hook`/`make_audit_hook`/`anonymize_hook` (existants, `agent/harness/hooks.py`).
- Produces : `build_ask_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions` ; `ASK_SYSTEM_PROMPT: str` ; `BUSINESS_ANALYST_SYSTEM_PROMPT: str` — consommés par la Task 3.

- [ ] **Step 1: Écrire les tests (échouent, `build_ask_options` n'existe pas encore)**

Ajouter à la fin de `tests/unit/test_harness_options.py` (après les imports existants, ajouter `AgentDefinition` n'est pas nécessaire côté test — on inspecte juste les attributs) :

```python
from agent.harness.options import (
    ASK_SYSTEM_PROMPT,
    BUSINESS_ANALYST_SYSTEM_PROMPT,
    build_ask_options,
)


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
```

- [ ] **Step 2: Lancer les tests, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_options.py -v -k ask`
Expected: FAIL — `ImportError: cannot import name 'ASK_SYSTEM_PROMPT'`

- [ ] **Step 3: Implémenter `build_ask_options` dans `agent/harness/options.py`**

Modifier les imports en tête de fichier (ligne 3-14 actuelles) :

```python
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.hooks import (
    anonymize_hook,
    make_audit_hook,
    make_budget_guard_hook,
    make_tenant_scope_hook,
)
from agent.tools.biz_server import build_biz_mcp_server
from agent.tools.fs_scan_server import build_fs_scan_mcp_server
from agent.tools.mcp_server import build_obs_mcp_server
from discovery.scanner import DiscoveryReport
```

Ajouter à la fin du fichier (après `build_discovery_options`) :

```python
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
```

- [ ] **Step 4: Lancer les tests, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: PASS — tous les tests, existants et nouveaux.

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/options.py tests/unit/test_harness_options.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/options.py tests/unit/test_harness_options.py
git commit -m "feat: build_ask_options() — agent routeur + sous-agents diagnostic-investigator/business-analyst"
```

---

### Task 3 : Câblage du preset `ask` dans le runner

**Files:**
- Modify: `agent/harness/runner.py`
- Test: `tests/unit/test_harness_runner.py`

**Interfaces:**
- Consumes : `build_ask_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions` (Task 2).
- Produces : `run_agent("ask", user_message, tenant_id=..., endpoint=...) -> str` fonctionnel — consommé par les Tasks 4 et 5.

- [ ] **Step 1: Écrire les tests (échouent, `"ask"` absent des dicts de dispatch)**

Ajouter à la fin de `tests/unit/test_harness_runner.py` :

```python
@pytest.mark.asyncio
async def test_run_agent_mock_ask_returns_text(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("ask", "question", tenant_id="acme", endpoint="ask")
    assert "mock" in answer.lower()


@pytest.mark.asyncio
async def test_run_agent_ask_preset_dispatches_to_build_ask_options(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    captured = {}

    def fake_build_ask_options(tenant_id, system_prompt=None):
        captured["tenant_id"] = tenant_id
        return runner.build_triage_options(tenant_id, system_prompt=system_prompt)

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(result="ok", usage={"input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setitem(runner._PRESET_BUILDERS, "ask", fake_build_ask_options)

    answer = await runner.run_agent("ask", "question", tenant_id="acme", endpoint="ask")

    assert answer == "ok"
    assert captured["tenant_id"] == "acme"
```

- [ ] **Step 2: Lancer les tests, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v -k ask`
Expected: FAIL — `KeyError: 'ask'` dans `_MOCK_ANSWERS[preset]` (premier test).

- [ ] **Step 3: Câbler le preset `ask`**

Modifier les imports en tête de `agent/harness/runner.py` (ligne 6-11 actuelles) :

```python
from agent.harness.options import (
    build_ask_options,
    build_diagnostic_options,
    build_discovery_options,
    build_taxonomy_options,
    build_triage_options,
)
```

Modifier `_MOCK_ANSWERS` (ligne 15-28 actuelles) en ajoutant une entrée :

```python
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
```

Modifier `_PRESET_BUILDERS` (ligne 30-35 actuelles) :

```python
_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
    "triage": build_triage_options,
    "taxonomy": build_taxonomy_options,
    "discovery": build_discovery_options,
    "ask": build_ask_options,
}
```

- [ ] **Step 4: Lancer les tests, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: PASS — tous les tests, existants et nouveaux.

- [ ] **Step 5: Vérification expérimentale du point ouvert du design (`disallowed_tools` racine vs `AgentDefinition.tools`)**

Ce n'est pas un test unitaire (le comportement de délégation est décidé par le vrai modèle, pas mockable) mais une vérification manuelle à faire avant de considérer cette tâche terminée, cohérente avec `Global Constraints` en tête de ce plan. C'est le premier point du plan où `run_agent("ask", ...)` est réellement câblé de bout en bout (preset enregistré dans `_PRESET_BUILDERS` depuis le Step 3 ci-dessus).

Run (nécessite `VIGIE_MOCK_LLM=0` et des identifiants Anthropic valides dans l'environnement) :
```bash
PYTHONPATH=. VIGIE_MOCK_LLM=0 .venv/bin/python -c "
import asyncio
from agent.harness.runner import run_agent

async def main():
    print(await run_agent('ask', 'Quel est le taux d\'erreur HTTP ces dernières 24h ?', tenant_id='default', endpoint='ask'))

asyncio.run(main())
"
```
Expected: une réponse cohérente (pas une erreur de type outil refusé/introuvable). Si le CLI renvoie une erreur indiquant que le sous-agent `diagnostic-investigator` n'a pas pu utiliser `query_loki`, l'hypothèse d'indépendance entre `disallowed_tools` racine et `AgentDefinition.tools` est fausse — repli documenté dans le design (§3.2) : retirer `disallowed_tools` de `build_ask_options` (Task 2) et compter uniquement sur `ASK_SYSTEM_PROMPT` pour dissuader l'agent racine d'appeler les outils directement. Si ce repli est nécessaire, mettre à jour `agent/harness/options.py`, ce fichier de plan et `docs/superpowers/specs/2026-07-09-harness-phase3-ask-orchestrator-design.md` en conséquence avant de continuer.

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check agent/harness/runner.py tests/unit/test_harness_runner.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add agent/harness/runner.py tests/unit/test_harness_runner.py
git commit -m "feat: runner route le preset ask vers build_ask_options"
```

---

### Task 4 : Migrer `/ask` vers l'agent orchestrateur

**Files:**
- Modify: `agent/routes/ask.py`
- Test: `tests/unit/test_routes_ask.py`

**Interfaces:**
- Consumes : `run_agent(preset: str, user_message: str, tenant_id: str = "default", endpoint: str = "ask", system_prompt: str | None = None) -> str` (Task 3, déjà existant dans sa forme générale).
- Produces : `POST /ask` — contrat HTTP inchangé (`{"question": str} -> {"answer": str, "tenant_id": str}`), consommé en l'état par `tests/integration/test_api.py` et `tests/isolation/test_non_fuite.py` (aucune modification nécessaire dans ces fichiers).

- [ ] **Step 1: Écrire le test (échoue, la route appelle encore `agent_loop`)**

Créer `tests/unit/test_routes_ask.py` :

```python
from fastapi.testclient import TestClient

from agent.main import app


def test_ask_route_calls_run_agent_with_ask_preset(monkeypatch):
    import agent.routes.ask as ask_module

    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["tenant_id"] = tenant_id
        captured["endpoint"] = endpoint
        return "réponse factice"

    monkeypatch.setattr(ask_module, "run_agent", fake_run_agent)

    with TestClient(app) as client:
        r = client.post(
            "/ask", json={"question": "pourquoi ça plante ?"}, headers={"X-Tenant-ID": "acme"}
        )

    assert r.status_code == 200
    assert r.json() == {"answer": "réponse factice", "tenant_id": "acme"}
    assert captured == {"preset": "ask", "tenant_id": "acme", "endpoint": "ask"}
```

- [ ] **Step 2: Lancer le test, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_routes_ask.py -v`
Expected: FAIL — `AttributeError: <module 'agent.routes.ask'> does not have the attribute 'run_agent'`

- [ ] **Step 3: Modifier `agent/routes/ask.py`**

Remplacer le contenu du fichier :

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.harness.runner import run_agent
from agent.middleware.tenant import get_tenant_id

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask(req: AskRequest, tenant_id: str = Depends(get_tenant_id)):
    answer = await run_agent("ask", req.question, tenant_id=tenant_id, endpoint="ask")
    return {"answer": answer, "tenant_id": tenant_id}
```

- [ ] **Step 4: Lancer le test, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_routes_ask.py -v`
Expected: PASS

- [ ] **Step 5: Vérifier l'absence de régression sur les tests existants de `/ask`**

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_api.py tests/isolation/test_non_fuite.py -v -k ask`
Expected: PASS — `test_ask_scoped_tenant`, `test_01_ask_scoped_alpha`, `test_02_ask_scoped_beta` toujours verts (mode mock, contrat HTTP inchangé).

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check agent/routes/ask.py tests/unit/test_routes_ask.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add agent/routes/ask.py tests/unit/test_routes_ask.py
git commit -m "feat: /ask délègue à l'agent orchestrateur ask au lieu du preset diagnostic figé"
```

---

### Task 5 : Migrer `mcp/explain_anomaly` vers l'agent orchestrateur

**Files:**
- Modify: `agent/mcp/server.py:1-15,113-128`
- Test: `tests/integration/test_mcp_client.py`

**Interfaces:**
- Consumes : `run_agent(preset: str, user_message: str, tenant_id: str = "default", endpoint: str = "ask") -> str` (Task 3).
- Produces : `POST /mcp/tools/explain_anomaly` — contrat HTTP inchangé (`{"anomaly_id": int | None, "question": str | None} -> {"tenant_id": str, "diagnosis": str}`).

- [ ] **Step 1: Écrire le test (échoue, la route appelle encore `agent_loop`)**

Ajouter à la fin de `tests/integration/test_mcp_client.py` :

```python
def test_proto_factory_explain_anomaly_uses_ask_preset(mcp_client, monkeypatch):
    import agent.mcp.server as mcp_server_module

    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["endpoint"] = endpoint
        return "diagnostic factice"

    monkeypatch.setattr(mcp_server_module, "run_agent", fake_run_agent)

    r = mcp_client.post(
        "/mcp/tools/explain_anomaly",
        json={"question": "Pic CPU hier ?"},
        headers={"Authorization": "Bearer test-mcp"},
    )

    assert r.status_code == 200
    assert r.json()["diagnosis"] == "diagnostic factice"
    assert captured == {"preset": "ask", "endpoint": "mcp/explain_anomaly"}
```

- [ ] **Step 2: Lancer le test, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_client.py -v -k explain_anomaly_uses_ask`
Expected: FAIL — `AttributeError: <module 'agent.mcp.server'> does not have the attribute 'run_agent'`

- [ ] **Step 3: Modifier `agent/mcp/server.py`**

Remplacer l'import (ligne 11 actuelle) :

```python
from agent.harness.runner import run_agent
```

Remplacer le corps de `explain_anomaly` (lignes 123-127 actuelles) :

```python
    diagnosis = await run_agent(
        "ask",
        f"Investigation structurée (FAITS/HYPOTHÈSES):\n{context}",
        tenant_id=tenant_id,
        endpoint="mcp/explain_anomaly",
    )
```

Le reste de la fonction (lignes 113-122 et 128) est inchangé.

- [ ] **Step 4: Lancer le test, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_client.py -v`
Expected: PASS — les 3 tests du fichier (dont l'existant `test_proto_factory_explain_anomaly`, toujours vert en mode mock).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/mcp/server.py tests/integration/test_mcp_client.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/mcp/server.py tests/integration/test_mcp_client.py
git commit -m "feat: mcp/explain_anomaly délègue à l'agent orchestrateur ask"
```

---

### Task 6 : Régression finale + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Aucune (vérification finale + documentation).

- [ ] **Step 1: Suite complète**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — tous les tests (existants + Tasks 1-5), aucune régression.

- [ ] **Step 2: Lint global**

Run: `.venv/bin/ruff check agent/ tests/ cli/`
Expected: `All checks passed!`

- [ ] **Step 3: Ajouter une entrée CHANGELOG**

Ajouter sous la section `## [Unreleased]` existante de `CHANGELOG.md` :

```markdown
- Ajoute l'agent orchestrateur `ask` (harness) : `/ask` et `mcp/explain_anomaly` délèguent désormais à deux sous-agents spécialisés (`diagnostic-investigator` pour Loki/Prometheus/Tempo, `business-analyst` pour KPIs/taxonomie métier via le nouveau serveur MCP `vigie-biz`) au lieu d'un unique preset diagnostic figé. Comble le trou fonctionnel où l'agent diagnostic n'avait jamais accès aux données métier. `report/daily` et l'alerting restent inchangés (preset diagnostic direct).
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog pour l'agent orchestrateur ask (Phase 3)"
```
