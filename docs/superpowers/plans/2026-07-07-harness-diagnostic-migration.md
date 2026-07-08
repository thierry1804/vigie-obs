# Harness Claude Agent SDK — Migration de l'agent diagnostic (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le socle LLM maison (`agent/services/llm_client.py` + boucle manuelle dans `agent/services/agent_loop.py`) par le Claude Agent SDK pour l'agent diagnostic (PEV) uniquement — première étape du plan de migration en 5 phases du design `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md`.

**Architecture:** Un module `agent/harness/` (options de preset, hooks de garde-fous, exécuteur) devient l'unique point de passage vers le LLM pour l'agent diagnostic. `agent/tools/mcp_server.py` expose les 3 outils existants (Loki/Prometheus/Tempo) via un serveur MCP in-process (`create_sdk_mcp_server`) lié au tenant courant. `agent_loop()` garde exactement sa signature actuelle — tous les appelants (`routes/ask.py`, `routes/report.py`, `services/alerting.py`, `mcp/server.py`) restent inchangés.

**Tech Stack:** Python 3.12, FastAPI, `claude-agent-sdk` (nouveau), `anthropic` (conservé pour triage/discovery/taxonomie tant que la Phase 2 n'est pas faite), pytest + pytest-asyncio + pytest-httpx.

**Portée de ce plan (rappel du design, §7) :** Ce plan couvre uniquement l'étape 1 de la migration (harness + diagnostic). Les étapes 2 à 5 (triage/discovery/taxonomie, nouveaux outils MCP `query_business_kpis`/`query_taxonomy`, agent orchestrateur `ask`, vrai serveur MCP externe) feront l'objet de plans séparés une fois cette étape validée en production — chaque étape du design produit un livrable testable indépendant.

**Fait confirmé par sondage du paquet réel (`claude-agent-sdk==0.2.111`, installé et inspecté dans un venv jetable) :**
- `query()`/`ClaudeSDKClient` pilotent le binaire CLI `claude` en sous-processus (recherché via `shutil.which("claude")`, sinon `~/.npm-global/bin/claude` ou `~/node_modules/.bin/claude`) — **pas d'appel HTTP direct**. D'où la nécessité de Node.js + `npm install -g @anthropic-ai/claude-code` dans l'image Docker (Task 1).
- `@tool(name, description, input_schema)` accepte un JSON Schema brut en `input_schema` — nécessaire ici pour préserver l'optionalité exacte des paramètres (le raccourci `{"param": type}` marque **tous** les champs comme requis, ce qui changerait le comportement par rapport à `agent/tools/registry.py`).
- Le décorateur `@tool` retourne un objet `SdkMcpTool` (champs `name`, `description`, `input_schema`, `handler`, `annotations`) **avant** tout passage par `create_sdk_mcp_server()` — permet de tester `.handler(args)` directement sans lancer un vrai serveur MCP.
- `StopHookInput` (hook `Stop`) ne transporte **aucune donnée d'usage token** — contrairement à ce qu'envisageait une version antérieure du design. L'usage réel est porté par le `ResultMessage` final de l'itérateur retourné par `query()` (champs `usage: dict | None`, `result: str | None`, `total_cost_usd`, `num_turns`). `run_agent()` doit donc extraire l'usage de ce message, pas d'un hook.
- Un hook `PreToolUse` bloque un appel outil en retournant `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}` ; retourner `{}` laisse l'appel passer normalement.
- `PreToolUseHookInput`/`PostToolUseHookInput` exposent `tool_name: str` et `tool_input: dict[str, Any]` (pas d'autres champs utiles ici).

**Faits confirmés par le spike réel (Task 2, exécuté) :**
- Convention de nommage confirmée : `mcp__<nom_serveur>__<nom_outil>` (observé : `mcp__vigie-obs-spike__ping`).
- `ResultMessage.usage` contient bien `input_tokens`/`output_tokens` (plus des clés supplémentaires : `cache_creation_input_tokens`, `cache_read_input_tokens`, `server_tool_use`, `service_tier`, `cache_creation`, `inference_geo`, `iterations`, `speed` — sans impact, `runner.py` lit uniquement les deux clés attendues via `.get()`).
- **Découverte non anticipée par le design initial** : en session non-interactive (le cas de VIGIE, service backend sans opérateur humain pour répondre à une invite), un appel outil MCP est **bloqué par défaut** faute de permission — le hook `PreToolUse` se déclenche bien (nommage confirmé) mais l'outil n'est jamais exécuté, `ResultMessage.result` contient un message d'erreur d'autorisation au lieu du résultat. `ClaudeAgentOptions` doit donc fixer explicitivement `permission_mode="bypassPermissions"` pour l'agent diagnostic — le contrôle d'accès réel reste assuré par nos propres hooks (`budget_guard`, `tenant_scope`), pas par les invites interactives du CLI, qui n'ont aucun sens dans un service headless. Répercuté dans la Task 5 ci-dessous.

## Global Constraints

- Python `>=3.12` (`pyproject.toml`).
- `ruff` : line-length 100, target `py312` — tout nouveau fichier doit passer `ruff check`.
- Tests : `pytest` avec `asyncio_mode = "auto"` — pas besoin de `@pytest.mark.asyncio` explicite mais les tests existants l'utilisent, on garde la convention pour cohérence.
- Mode mock obligatoire en CI : `VIGIE_MOCK_LLM=1` (posé par `tests/conftest.py`) — aucun test ne doit nécessiter le CLI `claude` réel ni de clé API.
- Docstrings de module en français, une ligne, style existant (`"""Boucle agentique Plan-Exécute-Vérifie."""`).
- Aucun changement de signature publique sur `agent_loop()` — tous les appelants existants (`agent/routes/ask.py`, `agent/routes/report.py`, `agent/services/alerting.py`, `agent/mcp/server.py`) doivent continuer à fonctionner sans modification.
- `agent/services/llm_client.py` reste en place à l'issue de ce plan (encore utilisé par `triage.py`, `discovery.py`, `taxonomy.py` — retiré uniquement en Phase 2).

---

## File Structure

- Create : `agent/tools/mcp_server.py` — outils Loki/Prometheus/Tempo en `SdkMcpTool`, liés à un tenant.
- Create : `agent/harness/__init__.py` — marqueur de package.
- Create : `agent/harness/hooks.py` — fabriques de hooks (budget, scoping tenant, audit).
- Create : `agent/harness/options.py` — preset `ClaudeAgentOptions` pour l'agent diagnostic.
- Create : `agent/harness/runner.py` — `run_agent()`, point d'entrée unique vers le SDK (ou le mock).
- Modify : `agent/services/agent_loop.py` — devient un wrapper fin sur `run_agent("diagnostic", ...)`.
- Modify : `agent/requirements.txt` — ajoute `claude-agent-sdk`.
- Modify : `agent/Dockerfile` — installe Node.js + le CLI `@anthropic-ai/claude-code`.
- Modify : `CHANGELOG.md` — entrée de version.
- Test : `tests/unit/test_mcp_server_tools.py`
- Test : `tests/unit/test_harness_hooks.py`
- Test : `tests/unit/test_harness_options.py`
- Test : `tests/unit/test_harness_runner.py`

---

### Task 1 : Dépendance SDK + image Docker

**Files:**
- Modify: `agent/requirements.txt`
- Modify: `agent/Dockerfile`

**Interfaces:**
- Produces: paquet Python `claude_agent_sdk` importable dans `.venv` ; CLI `claude` installé dans l'image Docker de l'agent (pas dans l'environnement de dev/CI, qui reste en mode mock).

- [ ] **Step 1: Ajouter la dépendance dans `agent/requirements.txt`**

Ouvrir `agent/requirements.txt` et ajouter une ligne (après `anthropic>=0.40`) :

```
claude-agent-sdk>=0.2.111
```

- [ ] **Step 2: Installer localement et vérifier l'import**

Run: `.venv/bin/pip install -r agent/requirements.txt`
Puis : `.venv/bin/python -c "import claude_agent_sdk; print(claude_agent_sdk.__name__)"`
Expected: affiche `claude_agent_sdk` sans erreur.

- [ ] **Step 3: Mettre à jour `agent/Dockerfile`**

Remplacer le contenu de `agent/Dockerfile` par :

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get purge -y npm && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ /app/agent/
COPY discovery/ /app/discovery/
COPY config/templates/ /app/config/templates/
COPY cli/ /app/cli/

ENV PYTHONPATH=/app
ENV VIGIE_DATA_DIR=/data
ENV VIGIE_MOCK_LLM=0

RUN mkdir -p /data

EXPOSE 8080
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

(On garde `npm` installé le temps du `npm install -g`, puis on le purge pour garder l'image légère — seul le binaire `claude` installé sous `/usr/lib/node_modules/.bin` ou équivalent doit rester sur le `PATH`. Si la purge de `npm` casse la résolution du binaire, retirer les lignes `apt-get purge -y npm && apt-get autoremove -y` et garder npm dans l'image.)

- [ ] **Step 4: Construire l'image et vérifier la présence du CLI**

Run: `docker build -f agent/Dockerfile -t vigie-agent:harness-test .`
Puis : `docker run --rm vigie-agent:harness-test claude --version`
Expected: la commande affiche un numéro de version du CLI Claude Code (pas de `command not found`). Si l'environnement d'exécution n'a pas accès à `docker`, documenter dans la PR que cette étape doit être vérifiée en CI/CD avant merge — ne pas passer à la suite sans cette confirmation, au moins via les logs du pipeline de build existant.

- [ ] **Step 5: Commit**

```bash
git add agent/requirements.txt agent/Dockerfile
git commit -m "build: ajoute claude-agent-sdk et le CLI Claude Code à l'image agent"
```

---

### Task 2 : Spike — confirmer le comportement réel du SDK avant d'écrire le harness

**Files:**
- Create (temporaire, non commité) : `/tmp/vigie-sdk-spike.py`

**Interfaces:**
- Produces: confirmation écrite (dans le message de commit de la Task 3 suivante) de (a) la convention de nommage des outils MCP in-process vus par le modèle, (b) les clés du dict `ResultMessage.usage`. Ces deux faits sont consommés par `agent/harness/hooks.py` (Task 4, pattern du `matcher`) et `agent/harness/runner.py` (Task 6, extraction de l'usage).

Ce spike nécessite une vraie clé API (`ANTHROPIC_API_KEY` dans l'environnement) et le CLI `claude` installé localement (`npm install -g @anthropic-ai/claude-code`). Il fait un seul appel réel au modèle le moins cher disponible pour limiter le coût.

- [ ] **Step 1: Écrire le script de spike**

Créer `/tmp/vigie-sdk-spike.py` :

```python
"""Spike jetable — ne pas committer. Confirme deux faits avant d'écrire le harness :
1. La convention de nommage des outils MCP in-process vue par le modèle.
2. Les clés du dict ResultMessage.usage.
"""

import asyncio

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

observed_tool_names = []


@tool("ping", "Répond pong", {"type": "object", "properties": {}})
async def ping_tool(args):
    return {"content": [{"type": "text", "text": "pong"}]}


async def capture_tool_name_hook(input_data, tool_use_id, context):
    observed_tool_names.append(input_data["tool_name"])
    return {}


async def main():
    server = create_sdk_mcp_server("vigie-obs-spike", tools=[ping_tool])
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        system_prompt="Appelle l'outil ping une seule fois puis réponds 'ok'.",
        mcp_servers={"vigie-obs-spike": server},
        max_turns=3,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="mcp__vigie-obs-spike__.*", hooks=[capture_tool_name_hook])
            ]
        },
    )

    result_message = None
    async for message in query(prompt="Utilise l'outil ping.", options=options):
        if isinstance(message, ResultMessage):
            result_message = message

    print("=== Outils observés par le hook (matcher regex a bien matché) ===")
    print(observed_tool_names)
    assert observed_tool_names, "Le hook PreToolUse n'a jamais été déclenché — matcher à revoir"
    assert observed_tool_names[0].startswith("mcp__vigie-obs-spike__"), (
        f"Convention de nommage inattendue : {observed_tool_names[0]}"
    )

    print("=== ResultMessage.usage ===")
    print(result_message.usage)
    assert result_message is not None, "Aucun ResultMessage reçu"
    assert "input_tokens" in result_message.usage, result_message.usage
    assert "output_tokens" in result_message.usage, result_message.usage

    print("=== ResultMessage.result (texte final) ===")
    print(result_message.result)

    print("SPIKE OK — hypothèses du design confirmées.")


asyncio.run(main())
```

- [ ] **Step 2: Exécuter le spike**

Run: `ANTHROPIC_API_KEY=<clé réelle> python3 /tmp/vigie-sdk-spike.py`
Expected: se termine par `SPIKE OK — hypothèses du design confirmées.` sans `AssertionError`. Si une assertion échoue, noter la valeur réelle observée (nom d'outil exact, clés d'usage réelles) et ajuster les Tasks 4 et 6 ci-dessous en conséquence avant de continuer — ces tasks supposent que les deux hypothèses ci-dessus sont vraies.

- [ ] **Step 3: Supprimer le script jetable**

Run: `rm /tmp/vigie-sdk-spike.py`

(Pas de commit pour cette task — c'est une vérification, pas une livraison de code.)

---

### Task 3 : Serveur MCP in-process pour les outils d'observabilité

**Files:**
- Create: `agent/tools/mcp_server.py`
- Test: `tests/unit/test_mcp_server_tools.py`

**Interfaces:**
- Consumes: `run_query_loki(logql, hours_back, limit, tenant_id)` de `agent/tools/loki.py` ; `run_query_prometheus(promql, range_hours)` de `agent/tools/prometheus.py` ; `run_query_traces(trace_id, service, hours_back, limit, tenant_id)` de `agent/tools/traces.py` (signatures inchangées, déjà existantes).
- Produces: `build_obs_tools(tenant_id: str) -> list[SdkMcpTool]` et `build_obs_mcp_server(tenant_id: str) -> McpSdkServerConfig`, consommés par `agent/harness/options.py` (Task 5).

- [ ] **Step 1: Écrire le test des outils (avant l'implémentation)**

Créer `tests/unit/test_mcp_server_tools.py` :

```python
import re

import pytest

from agent.tools.mcp_server import build_obs_tools


def _tool_by_name(tenant_id, name):
    tools = build_obs_tools(tenant_id)
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_query_loki_tool_scopes_tenant(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={
            "data": {
                "result": [
                    {"stream": {"level": "error"}, "values": [["1700000000000000000", "timeout"]]}
                ]
            }
        },
    )
    tool = _tool_by_name("acme", "query_loki")
    result = await tool.handler({"logql": '{level="error"}'})
    text = result["content"][0]["text"]
    assert "timeout" in text


@pytest.mark.asyncio
async def test_query_prometheus_tool_optional_range(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": [{"value": [1, "42"]}]}},
    )
    tool = _tool_by_name("acme", "query_prometheus")
    result = await tool.handler({"promql": "up"})
    assert "42" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_query_traces_tool_no_traces(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://tempo:3200/api/search.*"),
        json={"traces": []},
    )
    tool = _tool_by_name("acme", "query_traces")
    result = await tool.handler({"service": "web"})
    assert "Aucune trace" in result["content"][0]["text"]


def test_query_loki_schema_requires_logql():
    tool = _tool_by_name("acme", "query_loki")
    assert tool.input_schema["required"] == ["logql"]


def test_query_prometheus_schema_requires_promql():
    tool = _tool_by_name("acme", "query_prometheus")
    assert tool.input_schema["required"] == ["promql"]


def test_query_traces_schema_has_no_required_fields():
    tool = _tool_by_name("acme", "query_traces")
    assert "required" not in tool.input_schema
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_mcp_server_tools.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'agent.tools.mcp_server'`.

- [ ] **Step 3: Implémenter `agent/tools/mcp_server.py`**

```python
"""Serveur MCP in-process (outils observabilité) — remplace agent/tools/registry.py."""

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus
from agent.tools.traces import run_query_traces


def build_obs_tools(tenant_id: str) -> list[SdkMcpTool[Any]]:
    """Construit les 3 outils d'observabilité liés à un tenant précis."""

    @tool(
        "query_loki",
        "Interroge les logs centralisés via LogQL. Labels : tenant_id, level, "
        "stream_type, business_event_type, trace_id.",
        {
            "type": "object",
            "properties": {
                "logql": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": ["logql"],
        },
    )
    async def query_loki_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_loki(
            logql=args["logql"],
            hours_back=args.get("hours_back", 24),
            limit=args.get("limit", 100),
            tenant_id=tenant_id,
        )
        return {"content": [{"type": "text", "text": result}]}

    @tool(
        "query_prometheus",
        "Exécute une requête PromQL instantanée ou de plage.",
        {
            "type": "object",
            "properties": {
                "promql": {"type": "string"},
                "range_hours": {"type": "number"},
            },
            "required": ["promql"],
        },
    )
    async def query_prometheus_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_prometheus(
            promql=args["promql"],
            range_hours=args.get("range_hours"),
        )
        return {"content": [{"type": "text", "text": result}]}

    @tool(
        "query_traces",
        "Interroge Tempo pour traces distribuées (si SDK OTel actif).",
        {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string"},
                "service": {"type": "string"},
                "hours_back": {"type": "number"},
                "limit": {"type": "integer"},
            },
        },
    )
    async def query_traces_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await run_query_traces(
            trace_id=args.get("trace_id"),
            service=args.get("service"),
            hours_back=args.get("hours_back", 24),
            limit=args.get("limit", 20),
            tenant_id=tenant_id,
        )
        return {"content": [{"type": "text", "text": result}]}

    return [query_loki_tool, query_prometheus_tool, query_traces_tool]


def build_obs_mcp_server(tenant_id: str) -> McpSdkServerConfig:
    """Serveur MCP in-process prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-obs", tools=build_obs_tools(tenant_id))
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_mcp_server_tools.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/tools/mcp_server.py tests/unit/test_mcp_server_tools.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/tools/mcp_server.py tests/unit/test_mcp_server_tools.py
git commit -m "feat: serveur MCP in-process pour les outils Loki/Prometheus/Tempo"
```

---

### Task 4 : Hooks de garde-fous (budget, scoping tenant, audit)

**Files:**
- Create: `agent/harness/__init__.py`
- Create: `agent/harness/hooks.py`
- Test: `tests/unit/test_harness_hooks.py`

**Interfaces:**
- Consumes: `check_budget(tenant_id) -> tuple[bool, str]` de `agent/services/tokens.py` ; `audit(tenant_id, action, detail)` de `agent/services/audit.py` (signatures inchangées).
- Produces: `make_budget_guard_hook(tenant_id) -> HookCallback`, `make_tenant_scope_hook(tenant_id) -> HookCallback`, `make_audit_hook(tenant_id) -> HookCallback`, consommés par `agent/harness/options.py` (Task 5).

- [ ] **Step 1: Créer le package**

Créer `agent/harness/__init__.py` :

```python
"""Harness agentique VIGIE — point de passage unique vers le Claude Agent SDK."""
```

- [ ] **Step 2: Écrire les tests des hooks**

Créer `tests/unit/test_harness_hooks.py` :

```python
import pytest

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.harness.hooks import make_audit_hook, make_budget_guard_hook, make_tenant_scope_hook


def _pre_tool_input(tool_input: dict) -> dict:
    return {
        "session_id": "s1",
        "transcript_path": "/tmp/t",
        "cwd": "/app",
        "agent_id": "a1",
        "agent_type": "diagnostic",
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__vigie-obs__query_loki",
        "tool_input": tool_input,
        "tool_use_id": "tu1",
    }


@pytest.mark.asyncio
async def test_budget_guard_allows_when_budget_ok():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", budget_llm_tokens=1000, tokens_used=0))
        session.commit()
    hook = make_budget_guard_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output == {}


@pytest.mark.asyncio
async def test_budget_guard_denies_when_budget_exhausted():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", budget_llm_tokens=1000, tokens_used=1000))
        session.commit()
    hook = make_budget_guard_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_tenant_scope_allows_own_tenant():
    hook = make_tenant_scope_hook("acme")
    output = await hook(_pre_tool_input({"logql": '{tenant_id="acme",level="error"}'}), "tu1", {})
    assert output == {}


@pytest.mark.asyncio
async def test_tenant_scope_denies_other_tenant():
    hook = make_tenant_scope_hook("acme")
    output = await hook(_pre_tool_input({"logql": '{tenant_id="beta",level="error"}'}), "tu1", {})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "beta" in output["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_audit_hook_writes_entry():
    from agent.db.models import AuditLog

    hook = make_audit_hook("acme")
    output = await hook(_pre_tool_input({"logql": "{}"}), "tu1", {})
    assert output == {}
    with get_session() as session:
        rows = session.query(AuditLog).filter(AuditLog.tenant_id == "acme").all()
    assert any(r.action == "tool_call" for r in rows)
```

- [ ] **Step 3: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_hooks.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'agent.harness.hooks'`.

- [ ] **Step 4: Implémenter `agent/harness/hooks.py`**

```python
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
    """PreToolUse : refuse toute requête LogQL/PromQL référençant un autre tenant_id."""

    tenant_pattern = re.compile(r'tenant_id="([^"]+)"')

    async def _tenant_scope_hook(input_data, tool_use_id, context):
        tool_input = input_data.get("tool_input", {})
        query_text = tool_input.get("logql") or tool_input.get("promql") or ""
        match = tenant_pattern.search(query_text)
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
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_hooks.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check agent/harness/ tests/unit/test_harness_hooks.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add agent/harness/__init__.py agent/harness/hooks.py tests/unit/test_harness_hooks.py
git commit -m "feat: hooks harness (budget, scoping tenant, audit)"
```

---

### Task 5 : Preset `ClaudeAgentOptions` pour l'agent diagnostic

**Files:**
- Create: `agent/harness/options.py`
- Test: `tests/unit/test_harness_options.py`

**Interfaces:**
- Consumes: `build_obs_mcp_server(tenant_id)` (Task 3) ; `make_budget_guard_hook`, `make_tenant_scope_hook`, `make_audit_hook` (Task 4) ; `MAX_TOOL_TURNS`, `MODEL_DIAGNOSTIC` de `agent/config.py`.
- Produces: `DIAGNOSTIC_SYSTEM_PROMPT: str`, `build_diagnostic_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions`, consommé par `agent/harness/runner.py` (Task 6).

- [ ] **Step 1: Écrire le test du preset**

Créer `tests/unit/test_harness_options.py` :

```python
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'agent.harness.options'`.

- [ ] **Step 3: Implémenter `agent/harness/options.py`**

```python
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
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/options.py tests/unit/test_harness_options.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/options.py tests/unit/test_harness_options.py
git commit -m "feat: preset ClaudeAgentOptions pour l'agent diagnostic"
```

---

### Task 6 : Exécuteur `run_agent()` (mock + réel)

**Files:**
- Create: `agent/harness/runner.py`
- Test: `tests/unit/test_harness_runner.py`

**Interfaces:**
- Consumes: `build_diagnostic_options(tenant_id, system_prompt)` (Task 5) ; `_mock_enabled()` de `agent/services/llm_client.py` (déjà existant, réutilisé tel quel) ; `record_usage(tenant_id, endpoint, model, input_tokens, output_tokens)` de `agent/services/tokens.py`.
- Produces: `async def run_agent(preset: str, user_message: str, tenant_id: str = "default", endpoint: str = "ask", system_prompt: str | None = None) -> str`, consommé par `agent/services/agent_loop.py` (Task 7).

- [ ] **Step 1: Écrire les tests du runner**

Créer `tests/unit/test_harness_runner.py` :

```python
import pytest

from agent.harness import runner


class FakeResultMessage:
    def __init__(self, result, usage):
        self.result = result
        self.usage = usage


@pytest.mark.asyncio
async def test_run_agent_mock_short_circuits(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")
    assert "Réponse mock VIGIE" in answer


@pytest.mark.asyncio
async def test_run_agent_real_path_extracts_final_text_and_records_usage(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(
            result="Diagnostic : latence Prometheus due à un pic de charge.",
            usage={"input_tokens": 120, "output_tokens": 45},
        )

    recorded = []

    def fake_record_usage(tenant_id, endpoint, model, input_tokens, output_tokens):
        recorded.append((tenant_id, endpoint, model, input_tokens, output_tokens))

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(runner, "record_usage", fake_record_usage)

    answer = await runner.run_agent(
        "diagnostic", "pourquoi ça plante ?", tenant_id="acme", endpoint="ask"
    )

    assert answer == "Diagnostic : latence Prometheus due à un pic de charge."
    assert recorded == [("acme", "ask", "claude-sonnet-4-6", 120, 45)]


@pytest.mark.asyncio
async def test_run_agent_real_path_no_result_message(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        return
        yield  # pragma: no cover - générateur vide

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)

    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")
    assert "Erreur" in answer
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'agent.harness.runner'`.

- [ ] **Step 3: Implémenter `agent/harness/runner.py`**

```python
"""Point d'entrée unique vers le LLM pour l'agent diagnostic — harness Claude Agent SDK."""

from claude_agent_sdk import ResultMessage, query

from agent.config import MODEL_DIAGNOSTIC
from agent.harness.options import build_diagnostic_options
from agent.services.llm_client import _mock_enabled
from agent.services.tokens import record_usage

MOCK_DIAGNOSTIC_ANSWER = (
    "Réponse mock VIGIE. FAITS : données simulées. HYPOTHÈSES : aucune conclusion réelle sans API."
)

_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
}


async def run_agent(
    preset: str,
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
) -> str:
    """Exécute un agent (preset donné) via le harness, ou renvoie une réponse fixture en mode mock."""
    if _mock_enabled():
        return MOCK_DIAGNOSTIC_ANSWER

    options = _PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt)

    result_message: ResultMessage | None = None
    async for message in query(prompt=user_message, options=options):
        if isinstance(message, ResultMessage):
            result_message = message

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
    return result_message.result or ""
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/runner.py tests/unit/test_harness_runner.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/runner.py tests/unit/test_harness_runner.py
git commit -m "feat: run_agent() — exécuteur harness avec court-circuit mock"
```

---

### Task 7 : Migrer `agent_loop()` vers le harness + suite de régression complète

**Files:**
- Modify: `agent/services/agent_loop.py`

**Interfaces:**
- Consumes: `run_agent(preset, user_message, tenant_id, endpoint, system_prompt)` (Task 6).
- Produces: `async def agent_loop(user_message: str, tenant_id: str = "default", endpoint: str = "ask", system_prompt: str | None = None) -> str` — signature **inchangée**, consommée par `agent/routes/ask.py`, `agent/routes/report.py`, `agent/services/alerting.py`, `agent/mcp/server.py` (aucune modification requise dans ces 4 fichiers).

- [ ] **Step 1: Remplacer le contenu de `agent/services/agent_loop.py`**

```python
"""Boucle agentique diagnostic — délègue au harness (Claude Agent SDK)."""

from agent.harness.runner import run_agent


async def agent_loop(
    user_message: str,
    tenant_id: str = "default",
    endpoint: str = "ask",
    system_prompt: str | None = None,
) -> str:
    return await run_agent(
        "diagnostic",
        user_message,
        tenant_id=tenant_id,
        endpoint=endpoint,
        system_prompt=system_prompt,
    )
```

- [ ] **Step 2: Lancer la suite de tests complète**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — tous les tests existants (`tests/integration/test_api.py`, `tests/isolation/test_non_fuite.py`, `tests/unit/test_tools.py`, `tests/integration/test_discovery.py`, `tests/integration/test_discovery_config.py`, `tests/integration/test_mcp_client.py`) plus les nouveaux tests des Tasks 3 à 6, sans régression. Ces tests passent en mode mock donc ne nécessitent ni CLI `claude` ni clé API.

- [ ] **Step 3: Lint global**

Run: `.venv/bin/ruff check agent/ tests/`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add agent/services/agent_loop.py
git commit -m "refactor: agent_loop() délègue au harness Claude Agent SDK"
```

---

### Task 8 : CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Aucune (documentation).

- [ ] **Step 1: Lire l'entête actuel de `CHANGELOG.md`**

Ouvrir `CHANGELOG.md` et repérer la section la plus récente (format existant à respecter).

- [ ] **Step 2: Ajouter une entrée**

Ajouter en haut du fichier (sous le titre principal, avant la première entrée existante) :

```markdown
## [Unreleased]

### Changed
- L'agent diagnostic (`/ask`, alerting, `mcp/explain_anomaly`) délègue désormais au Claude Agent SDK via `agent/harness/` au lieu d'un client Anthropic maison. Comportement externe inchangé (mêmes endpoints, même mode mock `VIGIE_MOCK_LLM=1`). Corrige au passage un blocage de la boucle événements asyncio pendant les appels LLM.
- L'image Docker de l'agent embarque désormais Node.js et le CLI `@anthropic-ai/claude-code`, requis par le nouveau harness.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog pour la migration harness de l'agent diagnostic"
```

---

## Self-Review (fait par l'auteur du plan)

1. **Couverture du design** : §2/§3.1/§3.2/§3.3 (partiel — outils existants seulement) du design sont couverts par les Tasks 1, 3, 4, 5, 6. §3.4 (agent orchestrateur `ask`) et le reste de §3.3 (`query_business_kpis`/`query_taxonomy`) sont explicitement hors périmètre de ce plan (Phase 2+, cf. section "Portée" en tête de document). §5 (dégradation gracieuse, correction du blocage asyncio) est couvert par le choix de `query()` (asyncio natif) en Task 6. §6 (tests) est couvert par les Tasks 3-4-6 (mock court-circuité, hooks testés unitairement) — le test de non-fuite étendu au niveau hook (mentionné en §6 du design) est différé à la Phase 2, quand tous les agents passeront par les hooks (actuellement seul le diagnostic le fait ; `tests/isolation/test_non_fuite.py` continue de passer tel quel car il ne teste pas les hooks directement).
2. **Placeholders** : aucun trouvé — chaque step contient du code complet ou une commande+résultat attendu explicite.
3. **Cohérence des types/signatures** : `run_agent(preset, user_message, tenant_id, endpoint, system_prompt)` (Task 6) correspond exactement à l'appel dans `agent_loop()` (Task 7). `build_diagnostic_options(tenant_id, system_prompt=None)` (Task 5) correspond à l'appel dans `_PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt)` (Task 6). `build_obs_mcp_server(tenant_id)` (Task 3) correspond à l'appel dans `build_diagnostic_options` (Task 5).
