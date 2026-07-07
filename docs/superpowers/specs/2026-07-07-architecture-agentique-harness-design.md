# VIGIE — Architecture agentique cible (harness + multi-agents spécialisés)

Statut : Design validé | 2026-07-07
Réfère à : `VIGIE_Specs_Cible.md` (architecture produit V1/V2), `agent/services/agent_loop.py` (implémentation actuelle)

## 1. Contexte et motivation

L'agent VIGIE (V0→V2) appelle aujourd'hui l'API Anthropic à la main dans `agent/services/llm_client.py` et `agent/services/agent_loop.py` : boucle de tours manuelle (`MAX_TOOL_TURNS`), dispatch d'outils manuel (`agent/tools/registry.py`), pas de gestion de session, pas de hooks, pas de sous-agents. Chaque service LLM (diagnostic, triage, discovery, taxonomie) réimplémente sa propre gestion d'erreurs et de parsing de sortie.

Ce document définit la migration de ce socle vers le **Claude Agent SDK** comme harness, avec une décomposition en **agents spécialisés** plutôt qu'un agent unique enrichi.

**Note d'implémentation (validée après sondage du paquet réel `claude-agent-sdk==0.2.111`)** : ce SDK ne fait pas d'appel HTTP direct à l'API Anthropic — il pilote le binaire CLI `claude` (Claude Code) en sous-processus via un transport stdio JSON (`query()`/`ClaudeSDKClient` lèvent `CLINotFoundError` si le CLI n'est pas installé). Conséquences actées et acceptées pour ce projet :

- L'image Docker de l'agent (`agent/Dockerfile`) doit installer Node.js + le paquet npm `@anthropic-ai/claude-code`, en plus de Python.
- Chaque appel LLM lance un sous-processus CLI (coût de démarrage par tour, à multiplier par tenant et par cycle d'alerting) — accepté en échange des hooks/sous-agents/sessions natifs du SDK.
- Le mode mock (`VIGIE_MOCK_LLM=1`) reste un court-circuit dans `run_agent()` **avant** tout appel au SDK — jamais besoin d'installer le CLI en CI/tests.
- La correction du blocage asyncio (§1, point 1) reste valide : `query()` est un itérateur async, communication non bloquante avec le sous-processus.

Deux problèmes concrets motivent aussi cette migration, indépendamment du choix stratégique :

1. **Bug de blocage asyncio** : `agent_loop()` est `async def` mais `create_message()` effectue un appel HTTP synchrone bloquant à l'API Anthropic. Chaque investigation diagnostic gèle la boucle événements FastAPI, bloquant tous les tenants concurrents. Le SDK est asyncio-natif et corrige ceci sans effort dédié.
2. **Trou fonctionnel** : l'agent diagnostic (PEV) n'a accès qu'à Loki/Prometheus/Tempo — aucun moyen d'interroger la taxonomie métier ou les KPIs business pendant une investigation, alors que ces données existent déjà (exploitées uniquement côté serveur MCP externe).

## 2. Architecture cible

```
┌─────────────────────────────────────────────────────────────────┐
│  agent/harness/                                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────────┐    │
│  │ options.py    │  │ hooks.py      │  │ runner.py           │    │
│  │ (presets par  │  │ - budget      │  │ run_agent(preset,   │    │
│  │  agent : sys  │  │ - tenant-scope│  │   messages, ctx)    │    │
│  │  prompt/model/│  │ - audit log   │  │ - court-circuite    │    │
│  │  tools/turns) │  │               │  │   en mode mock LLM  │    │
│  └──────────────┘  └───────────────┘  └────────────────────┘    │
│           tous s'appuient sur le Claude Agent SDK (async natif)  │
└─────────────────────┬─────────────────────────────────────────--┘
                       │
     ┌─────────────────┼──────────────────┬──────────────────┐
     ▼                 ▼                  ▼                  ▼
┌─────────┐      ┌───────────┐      ┌───────────┐     ┌─────────────┐
│ Discovery│      │  Triage   │      │ Taxonomie │     │ Diagnostic   │
│  agent   │      │  agent    │      │  agent    │     │  agent (PEV) │
│ (Haiku,  │      │ (Haiku,   │      │ (Sonnet,  │     │ (Sonnet,     │
│ read-only│      │ 0 tool,   │      │ read Loki)│     │  Loki+Prom+  │
│ fs/logs) │      │ JSON out) │      │           │     │  Tempo +     │
│          │      │           │      │           │     │  KPIs/taxo)  │
└─────────┘      └───────────┘      └───────────┘     └──────┬──────┘
                                                               │ tool calls
                                                       ┌───────▼────────┐
                                                       │ agent/tools/    │
                                                       │ mcp_server.py   │
                                                       │ (serveur MCP    │
                                                       │  in-process via │
                                                       │  create_sdk_    │
                                                       │  mcp_server)    │
                                                       └────────────────┘

  Orchestration inter-agents = code Python déterministe (services/*),
  SAUF le cas "ask" détaillé en §3.4 (délégation LLM réelle).

┌────────────────────────────────────────────────────────────────┐
│ agent/mcp/server.py — devient un VRAI serveur MCP               │
│ (transport Streamable HTTP, protocole MCP réel, plus du REST     │
│  qui imite des noms d'outils MCP)                                │
│ consommé par agents ETECH externes (proto-factory, etc.)         │
└────────────────────────────────────────────────────────────────┘
```

**Principe directeur** : le SDK devient le seul point de passage vers le LLM. Chaque agent spécialisé est un preset `ClaudeAgentOptions` déclaré une fois (prompt système, modèle, outils autorisés, `max_turns`). L'orchestration entre agents reste du code Python explicite — sauf pour la délégation `ask`, seul point où la décision ("question technique ou métier ?") relève d'un jugement en langage libre et non d'un seuil numérique.

## 3. Composants

### 3.1 `agent/harness/options.py`

Un dict de presets, un par agent :

```python
PRESETS = {
    "discovery": ClaudeAgentOptions(model=MODEL_TRIAGE, system_prompt=DISCOVERY_PROMPT,
                                     mcp_servers={"vigie-fs": fs_readonly_server}, max_turns=4),
    "triage":    ClaudeAgentOptions(model=MODEL_TRIAGE, system_prompt=TRIAGE_PROMPT, max_turns=1),
    "taxonomy":  ClaudeAgentOptions(model=MODEL_DIAGNOSTIC, system_prompt=TAXONOMY_PROMPT,
                                     mcp_servers={"vigie-obs": obs_server}, max_turns=2),
    "diagnostic": ClaudeAgentOptions(model=MODEL_DIAGNOSTIC, system_prompt=PEV_PROMPT,
                                      mcp_servers={"vigie-obs": obs_server}, max_turns=MAX_TOOL_TURNS),
}
```

`discovery` utilise un serveur MCP **distinct** (`vigie-fs`, read-only filesystem/log-sampling) — jamais les mêmes outils que ceux qui touchent Loki/Prometheus, pour garantir que le scan reste strictement read-only (invariant produit existant).

### 3.2 `agent/harness/hooks.py`

Remplace le code dupliqué dans chaque service :

- `PreToolUse` : vérifie `check_budget(tenant_id)` avant **chaque** appel outil (plus précis que la vérification unique en tête de boucle actuelle — coupe en cours d'investigation si le budget s'épuise après quelques tours) ; valide que toute requête LogQL/PromQL référence bien le `tenant_id` de la session, jamais un autre.
- `PostToolUse` : `audit()` automatique (remplace les appels manuels épars dans `alerting.py`).

`record_usage()` n'est **pas** un hook : `StopHookInput` (SDK réel, sondé) ne transporte aucune donnée d'usage token. L'usage est porté par le `ResultMessage` final de l'itérateur retourné par `query()` (champs `usage`, `total_cost_usd`, `num_turns`) — `run_agent()` (§3.2 `runner.py`) l'extrait directement à la fin de l'itération et appelle `record_usage()` une seule fois par appel d'agent.

### 3.3 `agent/tools/mcp_server.py`

Remplace `agent/tools/registry.py`. `query_loki`/`query_prometheus`/`query_traces` deviennent des `@tool` async montés via `create_sdk_mcp_server("vigie-obs", tools=[...])`. Deux outils **nouveaux** :

- `query_business_kpis`
- `query_taxonomy`

(logique reprise de `agent/mcp/server.py::get_business_kpis` / `taxonomy.load_taxonomy`) — comblent le trou fonctionnel identifié en §1.

### 3.4 Agent orchestrateur `ask` (seule délégation agent-à-agent réelle)

Nouveau preset `"ask"`, utilisé uniquement par `POST /ask` et `mcp/tools/explain_anomaly`, avec deux `AgentDefinition` en sous-agents :

- `diagnostic-investigator` : PEV complet, outils Loki/Prometheus/Tempo.
- `business-analyst` : taxonomie/KPIs, léger, pas de PEV multi-tours.

Le SDK gère la reprise de session (`resume`), ce qui permet d'offrir un historique multi-tours sur `/ask` (aujourd'hui chaque appel repart de zéro).

**Tout le reste** (cycle d'alerting piloté par seuils, discovery, apprentissage de taxonomie) reste orchestré par du code Python déterministe qui appelle directement le bon preset — cohérent avec l'invariant produit "le LLM n'analyse jamais le flux brut / pipelines déterministes" (VIGIE_Specs_Cible.md §9).

### 3.5 `agent/mcp/server.py` — vrai serveur MCP

Remplacé par un serveur MCP conforme au protocole (transport Streamable HTTP du SDK `mcp`), exposant les 4 outils existants (`get_project_health`, `query_incidents`, `get_business_kpis`, `explain_anomaly`) via JSON-RPC réel, au lieu du REST qui n'en imitait que les noms. `explain_anomaly` route vers l'agent `ask` ; les trois autres restent des handlers directs sans LLM.

## 4. Flux de données par cas d'usage

| Cas d'usage | Déclencheur | Chemin |
|---|---|---|
| `/ask` conversationnel | Requête HTTP | agent `ask` → délègue à `diagnostic-investigator` ou `business-analyst` selon la question |
| Cycle d'alerting | APScheduler (`ALERT_INTERVAL_MINUTES`) | `evaluate_rule()` (coût nul, inchangé) → preset `triage` (cache inchangé) → si anomalie : preset `diagnostic` directement (pas d'orchestrateur, décision déjà prise par le seuil) → Slack/email |
| `vigie discover` | CLI/endpoint | preset `discovery` (outils `vigie-fs` read-only) → génération `vector.toml` (Jinja2, inchangé) |
| `vigie taxonomy propose` | CLI/endpoint | preset `taxonomy` → écrit le YAML proposé (inchangé) |
| Appels MCP externes | Agent ETECH tiers | vrai serveur MCP → 3 outils directs sans LLM + `explain_anomaly` → agent `ask` |

## 5. Gestion d'erreurs et dégradation

- **Blocage asyncio corrigé** : le SDK (`ClaudeSDKClient`/`query()`) est asyncio-natif, remplace l'appel synchrone bloquant de `create_message()`.
- **Retries** : gérés nativement par le SDK (aucun retry aujourd'hui autour de l'appel Anthropic brut).
- **Dégradation gracieuse préservée** : si le SDK/l'API est indisponible, `run_agent()` renvoie une erreur explicite sans faire planter FastAPI ; la collecte Vector/Loki/Prometheus/Grafana continue indépendamment (déjà vrai aujourd'hui — invariant à ne pas casser, VIGIE_Specs_Cible.md §5).
- **Budget dépassé en cours d'investigation** : le hook `PreToolUse` stoppe la conversation avant le prochain appel outil, plutôt que de laisser filer jusqu'à `MAX_TOOL_TURNS`.

## 6. Tests

- **Mode mock (`VIGIE_MOCK_LLM=1`)** : court-circuité dans `harness/runner.py::run_agent()` — si le flag est actif, renvoie les fixtures existantes avant tout appel SDK. Comportement des tests existants préservé.
- **Test de non-fuite multi-tenant** (`tests/isolation/test_non_fuite.py`) : étendu pour vérifier que le hook `PreToolUse` bloque une requête LogQL/PromQL référençant un tenant différent — passe d'un test "au niveau service" à un test "au niveau hook", appliqué uniformément à tous les agents.
- **Nouveau test** : délégation correcte de l'agent `ask` (question technique → `diagnostic-investigator`, question métier → `business-analyst`).

## 7. Plan de migration (incrémental)

1. Introduire `agent/harness/` + migrer `agent_loop.py` (diagnostic) en premier — plus gros risque, plus gros gain (corrige le bug async), et les call sites existants (`alerting.py`, `mcp/server.py::explain_anomaly`) continuent de fonctionner sans changement d'API externe.
2. Migrer `triage.py`, `discovery.py`, `taxonomy.py` vers leurs presets ; retrait de `llm_client.py`.
3. Ajouter les outils `query_business_kpis`/`query_taxonomy` au serveur MCP interne.
4. Construire l'agent `ask` orchestrateur + sous-agents ; brancher `/ask` et `explain_anomaly` dessus.
5. Remplacer `agent/mcp/server.py` par le vrai serveur MCP protocolaire (dernier, car surface externe consommée par proto-factory — changement de transport à coordonner).

## 8. Hors périmètre de ce design

- Isolation multi-tenant physique (SQLite unique aujourd'hui, pas de séparation par schéma/instance) : les hooks de scoping ajoutent une défense en profondeur au niveau des appels outils, mais ne résolvent pas le partage de fichier SQLite sous-jacent. Sujet séparé.
- SDK OTel / traces Tempo (V2) : l'outil `query_traces` existe déjà et sera simplement migré tel quel dans `mcp_server.py`, sans changement de comportement.
