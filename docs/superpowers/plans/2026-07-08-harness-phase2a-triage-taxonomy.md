# Harness Phase 2a — Migration triage + enrichissement taxonomie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrer `triage.py` et `taxonomy.py` vers le harness Claude Agent SDK (`agent/harness/`) construit en Phase 1 — triage en simple remplacement de moteur, taxonomie enrichie en agent qui explore lui-même les logs via `query_loki`. Ajoute un hook générique de rédaction d'email (`anonymize_hook`), branché sur tous les presets qui exposent des outils (diagnostic + taxonomie).

**Architecture:** `agent/harness/options.py` gagne deux nouveaux presets (`build_triage_options`, `build_taxonomy_options`) ; `agent/harness/runner.py` route vers eux via `_PRESET_BUILDERS` et un court-circuit mock désormais sensible au preset ; `triage.py`/`taxonomy.py` appellent `run_agent()` au lieu de `create_message()` directement. `discovery.py` n'est pas touché (Phase 2b séparée).

**Tech Stack:** Python 3.12, `claude-agent-sdk` (déjà en place depuis la Phase 1), pytest + pytest-asyncio.

**Réfère à** : `docs/superpowers/specs/2026-07-08-harness-phase2a-triage-taxonomy-design.md` (design validé).

## Faits confirmés par sondage réel du SDK (avant ce plan, via un spike jetable identique dans l'esprit à la Task 2 de la Phase 1)

- `PostToolUseHookInput["tool_response"]` est une **liste** de blocs de contenu — `[{"type": "text", "text": "..."}]` — **jamais** enveloppée dans un dict `{"content": [...]}`.
- `PreToolUseHookSpecificOutput`/`PostToolUseHookSpecificOutput`'s `updatedMCPToolOutput` doit avoir **exactement la même forme** que `tool_response` (une liste de blocs), pas un dict. Un premier essai avec `{"content": [...]}` a cassé l'appel d'outil côté CLI (erreur `"e.reduce is not a function"` — le CLI applique `.reduce()` directement sur la valeur fournie, donc elle doit être itérable comme une liste de blocs, pas un dict). Confirmé par un run réel : avec la forme liste, le modèle voit bien le texte corrigé et jamais l'original.
- Convention de nommage des outils MCP (`mcp__<serveur>__<outil>`) et `permission_mode="bypassPermissions"` déjà confirmés et en place depuis la Phase 1 — inchangés.

## Global Constraints

- Python `>=3.12`, `ruff` line-length 100, target `py312` — tout fichier modifié doit passer `ruff check`.
- Docstrings de module en français, une ligne, style existant.
- Mode mock obligatoire en CI : `VIGIE_MOCK_LLM=1` — aucun test ne doit nécessiter le CLI `claude` réel ni de clé API.
- `agent/services/discovery.py` n'est pas touché dans ce plan (Phase 2b séparée) — `agent/services/llm_client.py` reste donc en place (encore utilisé par `discovery.py`).
- Aucun changement de signature publique sur `propose_taxonomy(tenant_id, days=7) -> dict` (consommé par `cli/__main__.py` inchangé).
- `triage_alert()` devient `async def` — signature change assumé, un seul call site (`agent/services/alerting.py`) à mettre à jour avec `await`.
- `validate_taxonomy`, `apply_taxonomy`, `diff_taxonomy`, `generate_vrl`, `load_taxonomy` (dans `agent/services/taxonomy.py`) restent strictement inchangés.

---

## File Structure

- Modify : `agent/harness/hooks.py` — ajoute `anonymize_hook` (fonction simple, pas de factory).
- Modify : `agent/harness/options.py` — ajoute `TRIAGE_PROMPT`, `TAXONOMY_SYSTEM_PROMPT`, `build_triage_options()`, `build_taxonomy_options()` ; modifie `build_diagnostic_options()` pour ajouter `anonymize_hook` à `PostToolUse`.
- Modify : `agent/harness/runner.py` — étend `_PRESET_BUILDERS`, court-circuit mock sensible au preset.
- Modify : `agent/services/triage.py` — `triage_alert()` devient `async`, appelle `run_agent()`.
- Modify : `agent/services/alerting.py` — ajoute `await` à l'appel de `triage_alert()`.
- Modify : `agent/services/taxonomy.py` — `propose_taxonomy()` devient agentique ; supprime `sample_logs()`/`anonymize()`.
- Modify : `CHANGELOG.md` — entrée de version.
- Test : `tests/unit/test_harness_hooks.py` (étendu)
- Test : `tests/unit/test_harness_options.py` (étendu)
- Test : `tests/unit/test_harness_runner.py` (étendu)
- Test (nouveau) : `tests/unit/test_triage.py`
- Test (nouveau) : `tests/unit/test_taxonomy.py`

---

### Task 1 : `anonymize_hook` (rédaction email générique)

**Files:**
- Modify: `agent/harness/hooks.py`
- Test: `tests/unit/test_harness_hooks.py`

**Interfaces:**
- Produces: `async def anonymize_hook(input_data, tool_use_id, context) -> dict` — fonction module-level, **pas** de factory (aucun état par tenant), consommée par `agent/harness/options.py` (Task 2) dans les listes `PostToolUse` de `build_diagnostic_options` et `build_taxonomy_options`.

- [ ] **Step 1: Écrire les tests**

Ajouter à la fin de `tests/unit/test_harness_hooks.py` :

```python
from agent.harness.hooks import anonymize_hook


def _post_tool_input(tool_response: list) -> dict:
    return {
        "session_id": "s1",
        "transcript_path": "/tmp/t",
        "cwd": "/app",
        "agent_id": "a1",
        "agent_type": "taxonomy",
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__vigie-obs__query_loki",
        "tool_input": {"logql": '{stream_type="business"}'},
        "tool_response": tool_response,
        "tool_use_id": "tu1",
    }


@pytest.mark.asyncio
async def test_anonymize_hook_redacts_email_in_tool_response():
    tool_response = [
        {"type": "text", "text": "contact jean.dupont@example.com pour plus d'infos"}
    ]
    output = await anonymize_hook(_post_tool_input(tool_response), "tu1", {})
    updated = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert updated == [{"type": "text", "text": "contact <email> pour plus d'infos"}]


@pytest.mark.asyncio
async def test_anonymize_hook_noop_when_no_email():
    tool_response = [{"type": "text", "text": "aucune donnée sensible ici"}]
    output = await anonymize_hook(_post_tool_input(tool_response), "tu1", {})
    assert output == {}


@pytest.mark.asyncio
async def test_anonymize_hook_noop_on_non_list_tool_response():
    input_data = _post_tool_input([])
    input_data["tool_response"] = "erreur brute non structurée"
    output = await anonymize_hook(input_data, "tu1", {})
    assert output == {}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_hooks.py -v`
Expected: FAIL avec `ImportError: cannot import name 'anonymize_hook'`.

- [ ] **Step 3: Implémenter `anonymize_hook` dans `agent/harness/hooks.py`**

Ajouter à la fin du fichier :

```python
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


async def anonymize_hook(input_data, tool_use_id, context):
    """PostToolUse : rédige les emails dans tout résultat d'outil MCP.

    tool_response et updatedMCPToolOutput partagent la même forme (une liste
    de blocs {"type": "text", "text": ...}), jamais enveloppée dans un dict
    {"content": [...]} — confirmé par un run réel : passer un dict casse
    l'appel d'outil côté CLI.
    """
    tool_response = input_data.get("tool_response")
    if not isinstance(tool_response, list):
        return {}

    changed = False
    updated_blocks = []
    for block in tool_response:
        text = block.get("text", "") if isinstance(block, dict) else ""
        if isinstance(block, dict) and block.get("type") == "text" and _EMAIL_RE.search(text):
            changed = True
            updated_blocks.append({**block, "text": _EMAIL_RE.sub("<email>", text)})
        else:
            updated_blocks.append(block)

    if not changed:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedMCPToolOutput": updated_blocks,
        }
    }
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_hooks.py -v`
Expected: PASS (13 tests : 10 existants + 3 nouveaux).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/hooks.py tests/unit/test_harness_hooks.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/hooks.py tests/unit/test_harness_hooks.py
git commit -m "feat: hook anonymize_hook — rédaction email générique PostToolUse"
```

---

### Task 2 : Presets `triage` et `taxonomy`

**Files:**
- Modify: `agent/harness/options.py`
- Test: `tests/unit/test_harness_options.py`

**Interfaces:**
- Consumes: `anonymize_hook` (Task 1) ; `make_budget_guard_hook`, `make_tenant_scope_hook`, `make_audit_hook` (déjà en place) ; `build_obs_mcp_server` (déjà en place) ; `MODEL_TRIAGE`, `MODEL_DIAGNOSTIC` de `agent/config.py`.
- Produces: `TRIAGE_PROMPT: str`, `TAXONOMY_SYSTEM_PROMPT: str`, `build_triage_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions`, `build_taxonomy_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions` — les trois consommés par `agent/harness/runner.py` (Task 3).

- [ ] **Step 1: Écrire les tests**

Modifier `tests/unit/test_harness_options.py` — remplacer l'import et le test de comptage de hooks, puis ajouter les nouveaux tests :

```python
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
```

(Ceci remplace le contenu actuel du fichier — l'import et les 4 premiers tests sont repris tels quels, seul l'ajout de `post_hooks`/l'assertion de comptage change dans `test_build_diagnostic_options_has_pretooluse_and_posttooluse_hooks`.)

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: FAIL — `test_build_diagnostic_options_has_pretooluse_and_posttooluse_hooks` échoue sur `len(post_hooks) == 2` (actuellement 1), et `ImportError` sur `build_triage_options`/`build_taxonomy_options`/`TRIAGE_PROMPT`/`TAXONOMY_SYSTEM_PROMPT`.

- [ ] **Step 3: Implémenter dans `agent/harness/options.py`**

Remplacer le contenu du fichier par :

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
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_harness_options.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/options.py tests/unit/test_harness_options.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/options.py tests/unit/test_harness_options.py
git commit -m "feat: presets triage et taxonomy + anonymize_hook sur diagnostic"
```

---

### Task 3 : Dispatch de preset et mock sensible au preset dans `run_agent()`

**Files:**
- Modify: `agent/harness/runner.py`
- Test: `tests/unit/test_harness_runner.py`

**Interfaces:**
- Consumes: `build_triage_options`, `build_taxonomy_options` (Task 2).
- Produces: `run_agent()` accepte désormais `preset` valant `"diagnostic"`, `"triage"` ou `"taxonomy"` — signature inchangée, consommée par `agent/services/triage.py` (Task 4) et `agent/services/taxonomy.py` (Task 5).

- [ ] **Step 1: Écrire les tests**

Ajouter à la fin de `tests/unit/test_harness_runner.py` :

```python
@pytest.mark.asyncio
async def test_run_agent_mock_triage_returns_json(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("triage", "contexte", tenant_id="acme", endpoint="triage")
    assert '"is_anomaly"' in answer


@pytest.mark.asyncio
async def test_run_agent_mock_taxonomy_returns_yaml(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("taxonomy", "explore", tenant_id="acme", endpoint="taxonomy")
    assert "events:" in answer
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: FAIL — `KeyError: 'triage'` (le dict mock ne connaît que `"diagnostic"`).

- [ ] **Step 3: Modifier `agent/harness/runner.py`**

Remplacer les lignes concernées :

```python
"""Point d'entrée unique vers le LLM — harness Claude Agent SDK."""

from claude_agent_sdk import ResultMessage, query

from agent.config import MODEL_DIAGNOSTIC
from agent.harness.options import (
    build_diagnostic_options,
    build_taxonomy_options,
    build_triage_options,
)
from agent.services.llm_client import _mock_enabled
from agent.services.tokens import check_budget, record_usage

_MOCK_ANSWERS = {
    "diagnostic": (
        "Réponse mock VIGIE. FAITS : données simulées. HYPOTHÈSES : aucune conclusion réelle sans API."
    ),
    "triage": '{"is_anomaly": true, "reason": "anomalie plausible (mock)"}',
    "taxonomy": (
        "events:\n"
        "  - name: order_created\n"
        "    patterns: ['commande créée', 'order created']\n"
        "    description: Commande créée (mock)\n"
    ),
}

_PRESET_BUILDERS = {
    "diagnostic": build_diagnostic_options,
    "triage": build_triage_options,
    "taxonomy": build_taxonomy_options,
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
        return _MOCK_ANSWERS[preset]

    ok, msg = check_budget(tenant_id)
    if not ok:
        return msg

    options = _PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt)

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

(Le corps de `run_agent()` est inchangé — seuls les imports, `_MOCK_ANSWERS` [remplace `MOCK_DIAGNOSTIC_ANSWER`], et `_PRESET_BUILDERS` changent.)

- [ ] **Step 4: Lancer tous les tests du runner pour vérifier qu'ils passent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_harness_runner.py -v`
Expected: PASS (8 tests : 6 existants + 2 nouveaux).

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/harness/runner.py tests/unit/test_harness_runner.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/harness/runner.py tests/unit/test_harness_runner.py
git commit -m "feat: run_agent() route triage/taxonomy, mock sensible au preset"
```

---

### Task 4 : Migrer `triage.py` vers le harness

**Files:**
- Modify: `agent/services/triage.py`
- Modify: `agent/services/alerting.py:96`
- Test (nouveau): `tests/unit/test_triage.py`

**Interfaces:**
- Consumes: `run_agent(preset, user_message, tenant_id, endpoint, system_prompt) -> str` (Task 3), preset `"triage"`.
- Produces: `async def triage_alert(tenant_id: str, signature: str, context: str) -> tuple[bool, str]` — signature devient **async** (changement assumé, seul call site à `agent/services/alerting.py:96`).

- [ ] **Step 1: Écrire le test (nouveau fichier, aucun test n'existe aujourd'hui pour `triage_alert`)**

Créer `tests/unit/test_triage.py` :

```python
import pytest

from agent.services.triage import triage_alert


@pytest.mark.asyncio
async def test_triage_alert_mock_returns_anomaly(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    is_anomaly, reason = await triage_alert("acme", "sig1", "erreur 500 répétée")
    assert is_anomaly is True
    assert reason == "anomalie plausible (mock)"


@pytest.mark.asyncio
async def test_triage_alert_uses_cache_on_second_call(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    await triage_alert("acme", "sig-cache", "contexte identique")
    is_anomaly, source = await triage_alert("acme", "sig-cache", "contexte identique")
    assert source == "cache"
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_triage.py -v`
Expected: FAIL — `TypeError: object tuple can't be used in 'await' expression` (la fonction actuelle est synchrone).

- [ ] **Step 3: Modifier `agent/services/triage.py`**

Remplacer le contenu du fichier par :

```python
"""Triage Haiku — qualification bruit vs anomalie."""

import json
from datetime import datetime, timedelta, timezone

from agent.db.models import TriageCache
from agent.db.session import get_session
from agent.harness.runner import run_agent


def _cache_get(tenant_id: str, signature: str) -> bool | None:
    with get_session() as session:
        row = (
            session.query(TriageCache)
            .filter(
                TriageCache.tenant_id == tenant_id,
                TriageCache.signature == signature,
                TriageCache.expires_at > datetime.now(timezone.utc),
            )
            .first()
        )
        if row:
            return not row.is_noise
        return None


def _cache_set(tenant_id: str, signature: str, is_noise: bool, hours: int = 24) -> None:
    with get_session() as session:
        session.add(
            TriageCache(
                tenant_id=tenant_id,
                signature=signature,
                is_noise=is_noise,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            )
        )
        session.commit()


async def triage_alert(tenant_id: str, signature: str, context: str) -> tuple[bool, str]:
    cached = _cache_get(tenant_id, signature)
    if cached is not None:
        return cached, "cache"

    text = await run_agent("triage", context, tenant_id=tenant_id, endpoint="triage")
    try:
        data = json.loads(text.strip().strip("`").replace("json", ""))
        is_anomaly = bool(data.get("is_anomaly", True))
        reason = data.get("reason", "")
    except (json.JSONDecodeError, TypeError):
        is_anomaly = "false" not in text.lower()
        reason = text[:200]
    _cache_set(tenant_id, signature, is_noise=not is_anomaly)
    return is_anomaly, reason
```

- [ ] **Step 4: Mettre à jour l'appelant dans `agent/services/alerting.py`**

Modifier la ligne 96 (dans `process_alert`, déjà `async def`) :

```python
    is_anomaly, reason = await triage_alert(tenant_id, sig, context)
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/unit/test_triage.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Lancer la suite complète pour vérifier l'absence de régression**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — en particulier `tests/isolation/test_non_fuite.py` (qui exerce le cycle d'alerting) toujours au vert.

- [ ] **Step 7: Lint**

Run: `.venv/bin/ruff check agent/services/triage.py agent/services/alerting.py tests/unit/test_triage.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add agent/services/triage.py agent/services/alerting.py tests/unit/test_triage.py
git commit -m "refactor: triage_alert() délègue au harness (async)"
```

---

### Task 5 : Migrer `taxonomy.py` — taxonomie agentique

**Files:**
- Modify: `agent/services/taxonomy.py`
- Test (nouveau): `tests/unit/test_taxonomy.py`

**Interfaces:**
- Consumes: `run_agent(preset, user_message, tenant_id, endpoint, system_prompt) -> str` (Task 3), preset `"taxonomy"`.
- Produces: `async def propose_taxonomy(tenant_id: str, days: int = 7) -> dict` — signature **inchangée**, consommée par `cli/__main__.py` (aucune modification requise).

- [ ] **Step 1: Écrire le test (nouveau fichier, aucun test n'existe aujourd'hui pour `propose_taxonomy`)**

Créer `tests/unit/test_taxonomy.py` :

```python
import pytest

from agent.services.taxonomy import propose_taxonomy


@pytest.mark.asyncio
async def test_propose_taxonomy_writes_proposed_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    result = await propose_taxonomy("acme", days=3)

    assert result["tenant_id"] == "acme"
    assert (tmp_path / "acme.proposed.yaml").exists()
    assert result["taxonomy"]["events"][0]["name"] == "order_created"


@pytest.mark.asyncio
async def test_propose_taxonomy_falls_back_on_unparseable_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None):
        return "texte libre : ceci n'est pas du YAML valide : [}"

    monkeypatch.setattr(taxonomy_module, "run_agent", fake_run_agent)

    result = await propose_taxonomy("acme", days=7)

    assert result["taxonomy"]["events"] == []
    assert "raw" in result["taxonomy"]
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_taxonomy.py -v`
Expected: FAIL — le premier test échoue car `sample_logs`/`run_query_loki` sont encore appelés et le mock actuel ne correspond pas au format attendu (ou `AttributeError` selon l'ordre d'implémentation ; dans tous les cas, pas encore au vert avant l'étape 3).

- [ ] **Step 3: Modifier `agent/services/taxonomy.py`**

Remplacer uniquement l'en-tête (imports) et `propose_taxonomy()` — **supprimer** `sample_logs()` et `anonymize()` — en gardant `validate_taxonomy`, `apply_taxonomy`, `diff_taxonomy`, `generate_vrl`, `load_taxonomy` strictement identiques :

```python
"""Service taxonomie métier apprise."""

from pathlib import Path

import yaml

from agent.harness.runner import run_agent

TAXONOMY_DIR = Path(__file__).resolve().parents[2] / "config" / "taxonomies"


async def propose_taxonomy(tenant_id: str, days: int = 7) -> dict:
    prompt = (
        f'Explore les logs métier (stream_type="business") des {days} derniers jours '
        f"(hours_back={days * 24}) via l'outil query_loki, puis propose une taxonomie."
    )
    text = await run_agent("taxonomy", prompt, tenant_id=tenant_id, endpoint="taxonomy")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = {"events": [], "raw": text}
    path = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return {"tenant_id": tenant_id, "path": str(path), "taxonomy": data}


def validate_taxonomy(tenant_id: str) -> dict:
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    if not proposed.exists():
        return {"valid": False, "error": "Aucune taxonomie proposée."}
    data = yaml.safe_load(proposed.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        return {"valid": False, "error": "Aucun événement défini."}
    for ev in events:
        if not ev.get("name") or not ev.get("patterns"):
            return {"valid": False, "error": f"Événement invalide: {ev}"}
    return {"valid": True, "events_count": len(events)}


def apply_taxonomy(tenant_id: str) -> str:
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    validated = TAXONOMY_DIR / f"{tenant_id}.yaml"
    data = yaml.safe_load(proposed.read_text(encoding="utf-8"))
    validated.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return generate_vrl(data)


def diff_taxonomy(tenant_id: str) -> str:
    import difflib

    current = TAXONOMY_DIR / f"{tenant_id}.yaml"
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    if not proposed.exists():
        return "Pas de proposition."
    if not current.exists():
        return proposed.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        current.read_text(encoding="utf-8").splitlines(keepends=True),
        proposed.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile="current",
        tofile="proposed",
    )
    return "".join(diff) or "Identique."


def generate_vrl(taxonomy: dict) -> str:
    lines = [
        "msg = downcase(string!(.message))",
        '.stream_type = "technical"',
        '.business_event_type = "unknown"',
    ]
    for ev in taxonomy.get("events", []):
        name = ev["name"]
        pats = ev.get("patterns", [])
        conds = " || ".join(f'contains(msg, "{p.lower()}")' for p in pats if not p.startswith("("))
        if conds:
            lines.append(f"if {conds} {{")
            lines.append('  .stream_type = "business"')
            lines.append(f'  .business_event_type = "{name}"')
            lines.append("}")
    return "\n".join(lines)


def load_taxonomy(tenant_id: str) -> dict | None:
    path = TAXONOMY_DIR / f"{tenant_id}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_taxonomy.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lancer la suite complète pour vérifier l'absence de régression**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — en particulier `tests/integration/test_discovery_config.py` (n'utilise pas taxonomy) et tout endpoint exposant `load_taxonomy` (`report.py`, `mcp/server.py`) toujours au vert, ces fonctions étant inchangées.

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check agent/services/taxonomy.py tests/unit/test_taxonomy.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add agent/services/taxonomy.py tests/unit/test_taxonomy.py
git commit -m "refactor: propose_taxonomy() devient agentique (interroge query_loki lui-même)"
```

---

### Task 6 : Régression finale + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Aucune (documentation + vérification finale).

- [ ] **Step 1: Suite complète**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — tous les tests (existants + nouveaux des Tasks 1-5), aucune régression.

- [ ] **Step 2: Lint global**

Run: `.venv/bin/ruff check agent/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Ajouter une entrée CHANGELOG**

Ajouter sous la section `## [Unreleased]` existante de `CHANGELOG.md` (déjà présente depuis la Phase 1) :

```markdown
- Migre `triage.py` vers le harness Claude Agent SDK (`run_agent("triage", ...)`) — `triage_alert()` devient asynchrone.
- Enrichit `taxonomy.py` : `propose_taxonomy()` devient un agent qui interroge lui-même `query_loki` au lieu d'un échantillonnage Python fixe.
- Ajoute `anonymize_hook` (rédaction email) sur tous les presets exposant des outils (diagnostic et taxonomie) — ferme un trou de confidentialité où l'agent diagnostic ne rédigeait jamais les emails présents dans les logs qu'il lit.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog pour la migration harness triage/taxonomie (Phase 2a)"
```

---

## Self-Review (fait par l'auteur du plan)

1. **Couverture du design** : §3.1-3.2 (presets) couverts par Task 2 ; §3.3 (`anonymize_hook`) par Task 1 ; §3.5 (`triage.py`) par Task 4 ; §3.6 (`taxonomy.py`) par Task 5 ; §4 (gestion d'erreurs) vérifié sans code additionnel nécessaire (le fallback YAML/JSON existant absorbe déjà les chaînes d'erreur du harness) ; §5 (tests) couvert par les nouveaux fichiers `test_triage.py`/`test_taxonomy.py` et les extensions des fichiers harness existants ; §6 (spike) réalisé avant l'écriture de ce plan, faits intégrés directement (pas de tâche de spike séparée à exécuter). §7 (hors périmètre) respecté : `discovery.py` n'apparaît dans aucune tâche.
2. **Placeholders** : aucun trouvé — chaque step contient du code complet ou une commande + résultat attendu explicite.
3. **Cohérence des types/signatures** : `run_agent("triage", ...)`/`run_agent("taxonomy", ...)` (Task 3) correspondent exactement aux appels dans `triage.py` (Task 4) et `taxonomy.py` (Task 5). `anonymize_hook` (Task 1, signature `(input_data, tool_use_id, context) -> dict`) correspond à son usage dans les listes `hooks=[...]` de `options.py` (Task 2), cohérent avec les autres hooks déjà en place. `propose_taxonomy(tenant_id, days=7) -> dict` (Task 5) reste identique à la signature consommée par `cli/__main__.py`.
