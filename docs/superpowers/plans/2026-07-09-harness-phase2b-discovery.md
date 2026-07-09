# Harness Phase 2b — Migration + enrichissement discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrer `agent/services/discovery.py` vers le harness Claude Agent SDK, en corrigeant au passage un bug réel (la réponse LLM d'`infer_formats()` est aujourd'hui calculée mais jamais utilisée) — la classification de format/framework par source devient réellement pilotée par l'agent, via deux nouveaux outils bornés (`sample_lines`, `set_framework_hint`) sans jamais accepter de chemin libre.

**Architecture:** Nouveau serveur MCP in-process `agent/tools/fs_scan_server.py`, lié à un `DiscoveryReport` (pas à un tenant_id) — les deux outils mutent ce report en place. Nouveau preset `build_discovery_options` dans `agent/harness/options.py`. `run_agent()` gagne un passthrough `**preset_kwargs` générique (nécessaire pour transmettre `report`). `discovery/scanner.py` (les 4 primitives de scan) reste strictement inchangé — seule la classification devient agentique.

**Tech Stack:** Python 3.12, `claude-agent-sdk` (déjà en place), pytest + pytest-asyncio.

**Réfère à** : `docs/superpowers/specs/2026-07-09-harness-phase2b-discovery-design.md` (design validé).

## Global Constraints

- Python `>=3.12`, `ruff` line-length 100, target `py312` — tout fichier modifié doit passer `ruff check`.
- Docstrings de module en français, une ligne, style existant.
- Mode mock obligatoire en CI : `VIGIE_MOCK_LLM=1` — aucun test ne doit nécessiter le CLI `claude` réel ni de clé API.
- `discovery/scanner.py` reste **strictement inchangé** — cette phase ne touche que la couche classification (`agent/services/discovery.py` + nouveaux fichiers harness).
- `scan_log_paths`/`scan_ports`/`scan_docker` ne deviennent **pas** des outils agentiques (décision explicite du design §1 : aucune valeur itérative démontrée). Seuls `sample_lines` et le nouvel outil `set_framework_hint` le deviennent.
- Aucun outil de cette phase n'accepte de chemin libre en entrée — `sample_lines`/`set_framework_hint` sont bornés par `source_index` (un entier, jamais un chemin).
- `run_discovery(target, tenant_id="default", existing_config=None) -> dict` — forme du dict retourné (`report`/`proposed_config`/`diff`) inchangée ; la fonction devient `async def` (impact sur les 2 call sites connus : `agent/routes/discover.py`, `cli/__main__.py`, mis à jour dans ce même plan).
- `build_discovery_options(tenant_id, system_prompt=None, *, report)` — `report` est **obligatoire, keyword-only** (sans lui, l'appel doit lever `TypeError` immédiatement, pas produire un no-op silencieux).

---

## File Structure

- Create : `agent/tools/fs_scan_server.py` — 2 outils (`sample_lines`, `set_framework_hint`) liés à un `DiscoveryReport`.
- Modify : `agent/harness/options.py` — ajoute `build_discovery_options`.
- Modify : `agent/harness/runner.py` — `run_agent()` gagne `**preset_kwargs` ; ajoute `"discovery"` à `_PRESET_BUILDERS`/`_MOCK_ANSWERS`.
- Modify : `agent/services/discovery.py` — `infer_formats()`/`run_discovery()` deviennent `async`, `infer_formats()` devient agentique.
- Modify : `agent/routes/discover.py` — ajoute `await`.
- Modify : `cli/__main__.py` — enveloppe l'appel dans `asyncio.run(...)`.
- Modify : `tests/integration/test_discovery_config.py` — devient async.
- Modify : `CHANGELOG.md` — entrée de version.
- Test : `tests/unit/test_fs_scan_server.py` (nouveau)
- Test : `tests/unit/test_harness_options.py` (étendu)
- Test : `tests/unit/test_harness_runner.py` (étendu)
- Test : `tests/unit/test_discovery.py` (nouveau)

---

### Task 1 : Serveur MCP `fs_scan_server` (outils discovery)

**Files:**
- Create: `agent/tools/fs_scan_server.py`
- Test: `tests/unit/test_fs_scan_server.py`

**Interfaces:**
- Consumes: `discovery.scanner.sample_lines(source: LogSource, max_lines: int = 20) -> None` (mute `source.sample_lines` en place, inchangé), `discovery.scanner.DiscoveryReport`/`LogSource` (dataclasses inchangées).
- Produces: `build_discovery_tools(report: DiscoveryReport) -> list[SdkMcpTool[Any]]` et `build_fs_scan_mcp_server(report: DiscoveryReport) -> McpSdkServerConfig`, consommés par `agent/harness/options.py` (Task 2).

- [ ] **Step 1: Écrire les tests**

Créer `tests/unit/test_fs_scan_server.py` :

```python
import pytest

from agent.tools.fs_scan_server import build_discovery_tools
from discovery.scanner import DiscoveryReport, LogSource


def _report_with_source(tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("ligne 1\nligne 2\nligne 3\n", encoding="utf-8")
    source = LogSource(path=str(tmp_path), glob=str(tmp_path / "*.log"))
    return DiscoveryReport(target=str(tmp_path), log_sources=[source])


def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_sample_lines_tool_resamples_existing_source(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "sample_lines")

    result = await tool.handler({"source_index": 0, "max_lines": 2})

    text = result["content"][0]["text"]
    assert "ligne 1" in text
    assert report.log_sources[0].sample_lines == ["ligne 1", "ligne 2"]


@pytest.mark.asyncio
async def test_sample_lines_tool_rejects_out_of_range_index(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "sample_lines")

    result = await tool.handler({"source_index": 5})

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_set_framework_hint_tool_updates_report(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "set_framework_hint")

    result = await tool.handler({"source_index": 0, "framework": "laravel"})

    assert report.log_sources[0].framework_hint == "laravel"
    assert "laravel" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_set_framework_hint_tool_rejects_out_of_range_index(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "set_framework_hint")

    result = await tool.handler({"source_index": 5, "framework": "laravel"})

    assert result["is_error"] is True


def test_build_discovery_tools_returns_two_tools(tmp_path):
    report = _report_with_source(tmp_path)
    tools = build_discovery_tools(report)
    assert {t.name for t in tools} == {"sample_lines", "set_framework_hint"}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_fs_scan_server.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'agent.tools.fs_scan_server'`.

- [ ] **Step 3: Implémenter `agent/tools/fs_scan_server.py`**

```python
"""Serveur MCP in-process (outils discovery) — bornés à un DiscoveryReport déjà scanné."""

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from discovery import scanner
from discovery.scanner import DiscoveryReport


def build_discovery_tools(report: DiscoveryReport) -> list[SdkMcpTool[Any]]:
    """Construit les 2 outils discovery liés à un DiscoveryReport précis."""

    @tool(
        "sample_lines",
        "Ré-échantillonne les lignes d'une source de logs déjà découverte.",
        {
            "type": "object",
            "properties": {
                "source_index": {"type": "integer"},
                "max_lines": {"type": "integer"},
            },
            "required": ["source_index"],
        },
    )
    async def sample_lines_tool(args: dict[str, Any]) -> dict[str, Any]:
        index = args["source_index"]
        if index < 0 or index >= len(report.log_sources):
            return {
                "content": [{"type": "text", "text": f"source_index invalide : {index}"}],
                "is_error": True,
            }
        source = report.log_sources[index]
        scanner.sample_lines(source, max_lines=args.get("max_lines", 20))
        text = "\n".join(source.sample_lines) or "Aucune ligne échantillonnée."
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "set_framework_hint",
        "Enregistre la conclusion de classification (framework) pour une source de logs.",
        {
            "type": "object",
            "properties": {
                "source_index": {"type": "integer"},
                "framework": {"type": "string"},
            },
            "required": ["source_index", "framework"],
        },
    )
    async def set_framework_hint_tool(args: dict[str, Any]) -> dict[str, Any]:
        index = args["source_index"]
        if index < 0 or index >= len(report.log_sources):
            return {
                "content": [{"type": "text", "text": f"source_index invalide : {index}"}],
                "is_error": True,
            }
        framework = args["framework"]
        report.log_sources[index].framework_hint = framework
        text = f"framework_hint mis à jour pour la source {index} : {framework}"
        return {"content": [{"type": "text", "text": text}]}

    return [sample_lines_tool, set_framework_hint_tool]


def build_fs_scan_mcp_server(report: DiscoveryReport) -> McpSdkServerConfig:
    """Serveur MCP in-process prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-fs", tools=build_discovery_tools(report))
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_fs_scan_server.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/tools/fs_scan_server.py tests/unit/test_fs_scan_server.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/tools/fs_scan_server.py tests/unit/test_fs_scan_server.py
git commit -m "feat: serveur MCP fs_scan_server (sample_lines, set_framework_hint)"
```

---

### Task 2 : Preset `discovery`

**Files:**
- Modify: `agent/harness/options.py`
- Test: `tests/unit/test_harness_options.py`

**Interfaces:**
- Consumes: `build_fs_scan_mcp_server(report)` (Task 1) ; `make_budget_guard_hook`, `make_audit_hook`, `anonymize_hook` (déjà en place) ; `MODEL_TRIAGE` de `agent/config.py`.
- Produces: `DISCOVERY_SYSTEM_PROMPT: str`, `build_discovery_options(tenant_id: str, system_prompt: str | None = None, *, report: DiscoveryReport) -> ClaudeAgentOptions`, consommé par `agent/harness/runner.py` (Task 3).

- [ ] **Step 1: Écrire les tests**

Remplacer le bloc d'imports en haut de `tests/unit/test_harness_options.py` par :

```python
import pytest

from agent.config import MAX_TOOL_TURNS, MODEL_DIAGNOSTIC, MODEL_TRIAGE
from agent.harness.options import (
    DIAGNOSTIC_SYSTEM_PROMPT,
    DISCOVERY_SYSTEM_PROMPT,
    TAXONOMY_SYSTEM_PROMPT,
    TRIAGE_PROMPT,
    build_diagnostic_options,
    build_discovery_options,
    build_taxonomy_options,
    build_triage_options,
)
from discovery.scanner import DiscoveryReport, LogSource
```

Puis ajouter à la fin du fichier :

```python
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: FAIL avec `ImportError: cannot import name 'build_discovery_options'`.

- [ ] **Step 3: Implémenter dans `agent/harness/options.py`**

Modifier les imports en haut du fichier (ajouter `DiscoveryReport` et `build_fs_scan_mcp_server`) :

```python
"""Presets ClaudeAgentOptions par agent spécialisé VIGIE."""

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

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
```

Ajouter à la fin du fichier (après `build_taxonomy_options`) :

```python
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
```

Note : pas de `make_tenant_scope_hook` sur ce preset — les outils `sample_lines`/`set_framework_hint` n'ont aucun paramètre `logql`/`promql`, ce hook serait un no-op silencieux et trompeur.

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: PASS (13 tests : 9 existants + 4 nouveaux).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/options.py tests/unit/test_harness_options.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/options.py tests/unit/test_harness_options.py
git commit -m "feat: preset discovery (build_discovery_options)"
```

---

### Task 3 : Passthrough `**preset_kwargs` et dispatch discovery dans `run_agent()`

**Files:**
- Modify: `agent/harness/runner.py`
- Test: `tests/unit/test_harness_runner.py`

**Interfaces:**
- Consumes: `build_discovery_options(tenant_id, system_prompt=None, *, report)` (Task 2).
- Produces: `run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **preset_kwargs) -> str` — signature étendue avec un passthrough générique, consommé par `agent/services/discovery.py` (Task 4) sous la forme `run_agent("discovery", ..., report=report)`.

- [ ] **Step 1: Écrire les tests**

Ajouter à la fin de `tests/unit/test_harness_runner.py` :

```python
@pytest.mark.asyncio
async def test_run_agent_mock_discovery_returns_text(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("discovery", "classify", tenant_id="acme", endpoint="discover")
    assert answer == "Classification terminée (mock)."


@pytest.mark.asyncio
async def test_run_agent_passes_preset_kwargs_to_builder(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    captured = {}

    def fake_build_discovery_options(tenant_id, system_prompt=None, **kwargs):
        captured.update(kwargs)
        return runner.build_triage_options(tenant_id, system_prompt=system_prompt)

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(result="ok", usage={"input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setitem(runner._PRESET_BUILDERS, "discovery", fake_build_discovery_options)

    sentinel = object()
    await runner.run_agent(
        "discovery", "classify", tenant_id="acme", endpoint="discover", report=sentinel
    )

    assert captured == {"report": sentinel}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: FAIL — `KeyError: 'discovery'` sur le premier test, `TypeError` (kwarg `report` inattendu) sur le second.

- [ ] **Step 3: Modifier `agent/harness/runner.py`**

Remplacer le contenu du fichier par :

```python
"""Point d'entrée unique vers le LLM — harness Claude Agent SDK."""

from claude_agent_sdk import ResultMessage, query

from agent.config import MODEL_DIAGNOSTIC
from agent.harness.options import (
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
}

_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
    "triage": build_triage_options,
    "taxonomy": build_taxonomy_options,
    "discovery": build_discovery_options,
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
```

(Seuls les imports, `_MOCK_ANSWERS`, `_PRESET_BUILDERS`, et la signature de `run_agent` — ajout de `**preset_kwargs` passé à `_PRESET_BUILDERS[preset](...)` — changent. Le reste du corps est identique.)

- [ ] **Step 4: Lancer tous les tests du runner pour vérifier qu'ils passent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: PASS (10 tests : 8 existants + 2 nouveaux).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/runner.py tests/unit/test_harness_runner.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/runner.py tests/unit/test_harness_runner.py
git commit -m "feat: run_agent() route discovery, passthrough **preset_kwargs"
```

---

### Task 4 : Migrer `discovery.py` — classification agentique

**Files:**
- Modify: `agent/services/discovery.py`
- Modify: `agent/routes/discover.py`
- Modify: `cli/__main__.py`
- Modify: `tests/integration/test_discovery_config.py`
- Test (nouveau): `tests/unit/test_discovery.py`

**Interfaces:**
- Consumes: `run_agent(preset, user_message, tenant_id, endpoint, system_prompt, **preset_kwargs) -> str` (Task 3), preset `"discovery"` avec kwarg `report=...`.
- Produces: `async def infer_formats(report: DiscoveryReport, tenant_id: str = "default") -> DiscoveryReport`, `async def run_discovery(target: str, tenant_id: str = "default", existing_config: Path | None = None) -> dict` — `run_discovery` change de synchrone à asynchrone (les 2 call sites connus sont mis à jour dans cette même tâche).

- [ ] **Step 1: Écrire les tests (nouveau fichier, aucun test n'existe aujourd'hui pour `infer_formats`)**

Créer `tests/unit/test_discovery.py` :

```python
import pytest

from agent.services.discovery import infer_formats
from discovery.scanner import DiscoveryReport, LogSource


@pytest.mark.asyncio
async def test_infer_formats_applies_agent_conclusion(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.discovery as discovery_module

    source = LogSource(path=str(tmp_path), glob=str(tmp_path / "*.log"), sample_lines=["{}"])
    report = DiscoveryReport(target=str(tmp_path), log_sources=[source])

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", **kwargs):
        kwargs["report"].log_sources[0].framework_hint = "laravel"
        return "Classification terminée."

    monkeypatch.setattr(discovery_module, "run_agent", fake_run_agent)

    result = await infer_formats(report, tenant_id="acme")

    assert result.log_sources[0].framework_hint == "laravel"


@pytest.mark.asyncio
async def test_run_discovery_skips_agent_when_no_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.discovery as discovery_module

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("run_agent must not be called when there are no log sources")

    monkeypatch.setattr(discovery_module, "run_agent", fail_if_called)

    result = await discovery_module.run_discovery(str(tmp_path), tenant_id="acme")

    assert result["report"]["log_sources"] == []
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_discovery.py -v`
Expected: FAIL — `TypeError: object DiscoveryReport can't be used in 'await' expression` (la fonction actuelle est synchrone et ne prend pas `tenant_id`).

- [ ] **Step 3: Modifier `agent/services/discovery.py`**

Remplacer les imports et `infer_formats`/`run_discovery` (garder `generate_vector_config` et `diff_config` strictement inchangés) :

```python
"""Service Discovery — inférence LLM + génération vector.toml."""

import difflib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agent.harness.runner import run_agent
from discovery.scanner import DiscoveryReport, discover_target, report_to_json

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"


async def infer_formats(report: DiscoveryReport, tenant_id: str = "default") -> DiscoveryReport:
    last_index = len(report.log_sources) - 1
    prompt = (
        "Voici les sources de logs déjà découvertes, avec un premier échantillon de lignes "
        "pour chacune :\n"
        f"{report_to_json(report)}\n\n"
        f"Pour chaque source (index 0 à {last_index}), détermine son format/framework et "
        "enregistre ta conclusion avec l'outil set_framework_hint. Si les échantillons sont "
        "insuffisants pour conclure, utilise sample_lines pour en obtenir plus avant de conclure."
    )
    await run_agent("discovery", prompt, tenant_id=tenant_id, endpoint="discover", report=report)
    return report


def generate_vector_config(report: DiscoveryReport, tenant_id: str = "default") -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    template = env.get_template("vector.toml.j2")
    sources = []
    for i, src in enumerate(report.log_sources):
        sources.append(
            {
                "name": f"source_{i}",
                "include": [src.glob.replace(str(Path(report.target)), f"/host/{Path(report.target).name}") if report.target in src.glob else src.glob],
                "framework": src.framework_hint,
            }
        )
    if not sources:
        sources = [
            {
                "name": "app_logs",
                "include": [f"/host/{Path(report.target).name}/var/log/*.log"],
                "framework": "symfony",
            }
        ]
    return template.render(sources=sources, tenant_id=tenant_id)


def diff_config(proposed: str, existing_path: Path | None) -> str:
    if not existing_path or not existing_path.exists():
        return proposed
    existing = existing_path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        existing.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile=str(existing_path),
        tofile="proposed_vector.toml",
    )
    return "".join(diff) or "Aucune différence."


async def run_discovery(
    target: str, tenant_id: str = "default", existing_config: Path | None = None
) -> dict:
    report = discover_target(target)
    if report.log_sources:
        report = await infer_formats(report, tenant_id=tenant_id)
    proposed = generate_vector_config(report, tenant_id=tenant_id)
    return {
        "report": report.to_dict(),
        "proposed_config": proposed,
        "diff": diff_config(proposed, existing_config),
    }
```

- [ ] **Step 4: Mettre à jour l'appelant dans `agent/routes/discover.py`**

Remplacer la ligne 20 :

```python
    result = await run_discovery(req.target, tenant_id=tenant_id, existing_config=existing)
```

- [ ] **Step 5: Mettre à jour l'appelant dans `cli/__main__.py`**

Remplacer la ligne `result = run_discovery(target, tenant_id=tenant, existing_config=existing)` dans la commande `discover` par :

```python
    result = asyncio.run(run_discovery(target, tenant_id=tenant, existing_config=existing))
```

(`asyncio` est déjà importé en haut du fichier, utilisé par la commande `taxonomy_cmd`.)

- [ ] **Step 6: Mettre à jour `tests/integration/test_discovery_config.py`**

Remplacer le contenu du fichier par :

```python
import pytest

from agent.services.discovery import run_discovery


@pytest.mark.asyncio
async def test_run_discovery_generates_config(tmp_path):
    target = tmp_path / "symfony"
    (target / "var" / "log").mkdir(parents=True)
    (target / "var" / "log" / "dev.log").write_text("test log\n", encoding="utf-8")
    result = await run_discovery(str(target), tenant_id="default")
    assert "proposed_config" in result
    assert "tenant_id" in result["proposed_config"]
    assert "[sinks.loki]" in result["proposed_config"]
```

- [ ] **Step 7: Lancer les nouveaux tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_discovery.py tests/integration/test_discovery_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Lancer la suite complète pour vérifier l'absence de régression**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — en particulier `tests/integration/test_discovery.py` (scanner, non touché) et `tests/isolation/test_non_fuite.py::test_10_report_daily_tenant_scope` (n'utilise pas discovery mais partage l'app FastAPI) toujours au vert.

- [ ] **Step 9: Lint**

Run: `.venv/bin/ruff check agent/services/discovery.py agent/routes/discover.py cli/__main__.py tests/unit/test_discovery.py tests/integration/test_discovery_config.py`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add agent/services/discovery.py agent/routes/discover.py cli/__main__.py tests/unit/test_discovery.py tests/integration/test_discovery_config.py
git commit -m "refactor: infer_formats()/run_discovery() délèguent au harness (async, agentique)"
```

---

### Task 5 : Régression finale + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Aucune (documentation + vérification finale).

- [ ] **Step 1: Suite complète**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — tous les tests (existants + nouveaux des Tasks 1-4), aucune régression.

- [ ] **Step 2: Lint global**

Run: `.venv/bin/ruff check agent/ tests/ cli/`
Expected: `All checks passed!`

- [ ] **Step 3: Ajouter une entrée CHANGELOG**

Ajouter sous la section `## [Unreleased]` existante de `CHANGELOG.md` (déjà présente depuis les Phases 1/2a) :

```markdown
- Migre `discovery.py` vers le harness Claude Agent SDK — `infer_formats()`/`run_discovery()` deviennent asynchrones. Corrige un bug où la réponse LLM était calculée mais jamais utilisée (seule une heuristique Python décidait du `framework_hint`) : la classification est désormais réellement pilotée par l'agent, via deux outils bornés (`sample_lines`, `set_framework_hint`) qui n'acceptent jamais de chemin arbitraire.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog pour la migration harness discovery (Phase 2b)"
```

---

## Self-Review (fait par l'auteur du plan)

1. **Couverture du design** : §3.1 (`fs_scan_server`) couvert par Task 1 ; §3.2 (preset `discovery`) par Task 2 ; §3.3 (passthrough `**preset_kwargs`) par Task 3 ; §3.4 (`discovery.py`) par Task 4 ; §4 (flux de données), §5 (gestion d'erreurs, dégradation gracieuse par construction — vérifié sans code additionnel) et §6 (tests) couverts à travers l'ensemble des tâches. §7 (hors périmètre) respecté : `discovery/scanner.py` n'apparaît dans aucune tâche en modification, `llm_client.py` n'est pas retiré.
2. **Placeholders** : aucun trouvé — chaque step contient du code complet ou une commande + résultat attendu explicite.
3. **Cohérence des types/signatures** : `build_discovery_options(tenant_id, system_prompt=None, *, report)` (Task 2) correspond exactement à l'appel `_PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt, **preset_kwargs)` (Task 3) avec `preset_kwargs={"report": report}` fourni par `agent/services/discovery.py::infer_formats()` (Task 4). `build_discovery_tools(report)`/`build_fs_scan_mcp_server(report)` (Task 1) correspondent à leur usage dans `build_discovery_options` (Task 2). `run_discovery()` devenant async est propagé de façon cohérente aux 2 call sites connus (`agent/routes/discover.py`, `cli/__main__.py`) dans la même tâche (Task 4) qui introduit le changement de signature — pas de call site oublié (confirmé par grep avant l'écriture du plan).
