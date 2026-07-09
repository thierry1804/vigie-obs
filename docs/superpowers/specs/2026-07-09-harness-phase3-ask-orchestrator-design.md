# VIGIE — Harness Phase 3 : agent orchestrateur `ask`

Statut : Design validé | 2026-07-09
Réfère à : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` (design cible harness/multi-agents, §3.3-3.4), `docs/superpowers/specs/2026-07-09-harness-phase2b-discovery-design.md` (Phase 2b, dernière phase mergée), `docs/superpowers/harness-migration-status.md` (suivi global)

## 1. Contexte et motivation

Les Phases 1/2a/2b ont migré diagnostic, triage, taxonomie et discovery vers le harness — toutes en orchestration Python déterministe (le service Python décide quel preset appeler, jamais un LLM qui délègue à un autre). Il reste un trou fonctionnel identifié dès le design cible (§1, point 2) : l'agent diagnostic n'a accès qu'à Loki/Prometheus/Tempo, jamais aux données métier (taxonomie, KPIs) — alors que `/ask` doit pouvoir répondre aussi bien à des questions techniques qu'à des questions métier.

Cette phase introduit le **seul point de délégation agent-à-agent réel** du design cible : un preset `"ask"` dont le rôle est de lire la question et de la déléguer à l'un de deux sous-agents spécialisés, plutôt que de router en Python (une question métier vs technique n'est pas classifiable de façon fiable par des règles fixes). Ferme la boucle sur `/ask` et `mcp/explain_anomaly`, les deux derniers appelants encore branchés directement sur `agent_loop()` (preset `diagnostic` figé).

## 2. Architecture

```
agent/tools/
  mcp_server.py       INCHANGÉ — serveur "vigie-obs" (query_loki/query_prometheus/query_traces)

  biz_server.py        NOUVEAU, in-process, lié à un tenant_id (comme mcp_server.py) :
    - query_business_kpis(hours_back)  → compte les logs par business_event_type sur la
                                           taxonomie active, logique reprise de
                                           agent/mcp/server.py::get_business_kpis
    - query_taxonomy()                  → taxonomy.load_taxonomy(tenant_id)
                                           Isolé de vigie-obs pour ne pas exposer
                                           silencieusement ces 2 outils aux presets
                                           diagnostic/taxonomy existants (voir §3.1)

agent/harness/
  options.py  + build_ask_options(tenant_id, system_prompt=None) -> ClaudeAgentOptions
                model=MODEL_DIAGNOSTIC, max_turns=MAX_TOOL_TURNS,
                mcp_servers={"vigie-obs": ..., "vigie-biz": ...},
                disallowed_tools=[5 noms d'outils] (agent racine : jamais d'appel direct),
                agents={"diagnostic-investigator": AgentDefinition(...),
                        "business-analyst": AgentDefinition(...)}
                hooks : PreToolUse=[budget_guard+tenant_scope sur vigie-obs,
                        budget_guard seul sur vigie-biz] ; PostToolUse=[audit+anonymize
                        sur les deux matchers]
  runner.py   ~ _PRESET_BUILDERS["ask"] = build_ask_options
                _MOCK_ANSWERS["ask"] = fixture

agent/routes/ask.py         ~ agent_loop() → run_agent("ask", ..., endpoint="ask")
agent/mcp/server.py          ~ explain_anomaly : agent_loop() → run_agent("ask", ...,
                                endpoint="mcp/explain_anomaly")

agent/services/agent_loop.py  INCHANGÉ — reste le point d'entrée pour report/daily et le
                               cycle d'alerting, tous deux figés sur le preset "diagnostic"
                               (pas de délégation agent-à-agent nécessaire pour ces flux
                               déterministes, cohérent avec le design cible §4)
```

## 3. Composants

### 3.1 `agent/tools/biz_server.py`

`build_biz_tools(tenant_id: str) -> list[SdkMcpTool[Any]]` et `build_biz_mcp_server(tenant_id: str) -> McpSdkServerConfig`, sur le modèle de `agent/tools/mcp_server.py`.

- `query_business_kpis(hours_back: number = 24)` : reprend la boucle `agent/mcp/server.py::get_business_kpis` (lignes 90-105) — pour chaque événement de `load_taxonomy(tenant_id)`, un appel `run_query_loki(f'{{business_event_type="{name}"}}', tenant_id=tenant_id, ...)`, comptage des lignes.
- `query_taxonomy()` : `load_taxonomy(tenant_id)`, retourne la taxonomie active telle quelle (ou un message explicite si aucune taxonomie n'existe encore pour ce tenant).

**Serveur séparé plutôt qu'extension de `vigie-obs`** : `build_diagnostic_options`/`build_taxonomy_options` utilisent `build_obs_mcp_server(tenant_id)` sans aucune restriction d'outils — ajouter les 2 nouveaux outils à ce même serveur les exposerait silencieusement à ces deux presets existants, sans que rien ne le demande. Un serveur `vigie-biz` isolé (mirroir du pattern déjà utilisé pour `vigie-fs` en Phase 2b) garde diagnostic/taxonomy strictement inchangés.

Ni `query_business_kpis` ni `query_taxonomy` n'exposent de paramètre `logql`/`promql` au modèle — le tenant est fermé par closure Python (même pattern que `build_obs_tools`), jamais un paramètre que le LLM pourrait manipuler. `make_tenant_scope_hook` n'a donc pas de prise ici et n'est pas appliqué sur ce matcher.

### 3.2 Preset `ask`

```python
_OBS_TOOL_NAMES = ["mcp__vigie-obs__query_loki", "mcp__vigie-obs__query_prometheus", "mcp__vigie-obs__query_traces"]
_BIZ_TOOL_NAMES = ["mcp__vigie-biz__query_business_kpis", "mcp__vigie-biz__query_taxonomy"]

def build_ask_options(tenant_id: str, system_prompt: str | None = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=MODEL_DIAGNOSTIC,
        system_prompt=system_prompt or ASK_SYSTEM_PROMPT,
        mcp_servers={"vigie-obs": build_obs_mcp_server(tenant_id), "vigie-biz": build_biz_mcp_server(tenant_id)},
        disallowed_tools=_OBS_TOOL_NAMES + _BIZ_TOOL_NAMES,
        agents={
            "diagnostic-investigator": AgentDefinition(
                description="Investigation technique (PEV) sur logs/métriques/traces.",
                prompt=DIAGNOSTIC_SYSTEM_PROMPT,
                tools=_OBS_TOOL_NAMES,
                maxTurns=MAX_TOOL_TURNS,
            ),
            "business-analyst": AgentDefinition(
                description="Analyse KPIs/taxonomie métier, léger, pas de boucle PEV.",
                prompt=BUSINESS_ANALYST_SYSTEM_PROMPT,
                tools=_BIZ_TOOL_NAMES,
                model=MODEL_TRIAGE,
                maxTurns=3,
            ),
        },
        max_turns=MAX_TOOL_TURNS,
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [
                HookMatcher(matcher=_OBS_TOOL_MATCHER, hooks=[make_budget_guard_hook(tenant_id), make_tenant_scope_hook(tenant_id)]),
                HookMatcher(matcher=_BIZ_TOOL_MATCHER, hooks=[make_budget_guard_hook(tenant_id)]),
            ],
            "PostToolUse": [
                HookMatcher(matcher=_OBS_TOOL_MATCHER, hooks=[make_audit_hook(tenant_id), anonymize_hook]),
                HookMatcher(matcher=_BIZ_TOOL_MATCHER, hooks=[make_audit_hook(tenant_id), anonymize_hook]),
            ],
        },
    )
```

`ASK_SYSTEM_PROMPT` instruit l'agent racine qu'il est un routeur pur : il ne répond jamais directement, délègue systématiquement à `diagnostic-investigator` (questions techniques : erreurs, latence, disponibilité, logs, métriques, traces) ou `business-analyst` (questions métier : KPIs, événements métier, taxonomie), et enchaîne les deux si la question mélange les deux registres. `BUSINESS_ANALYST_SYSTEM_PROMPT` (nouveau) décrit l'usage de `query_business_kpis`/`query_taxonomy` sur le modèle de `TAXONOMY_SYSTEM_PROMPT` déjà existant.

`business-analyst` utilise `MODEL_TRIAGE` (Haiku) plutôt que `MODEL_DIAGNOSTIC` : cohérent avec sa description "léger, pas de PEV multi-tours" — pas besoin du modèle le plus capable pour lire une taxonomie et compter des KPIs déjà agrégés.

**Point à vérifier expérimentalement pendant l'implémentation** (pas supposé, à confirmer par un run réel comme les faits §4 du doc de suivi) : que `disallowed_tools` au niveau racine de `ClaudeAgentOptions` n'empêche pas les sous-agents définis via `agents=` d'utiliser ces mêmes outils dans leur propre contexte d'exécution. Aucun preset existant n'utilise aujourd'hui `disallowed_tools` ni `agents` — c'est un mécanisme SDK neuf pour ce projet. Si l'hypothèse s'avère fausse, alternative de repli : ne pas restreindre l'agent racine via `disallowed_tools` (accepter qu'il ait techniquement accès aux 5 outils) et compter uniquement sur `ASK_SYSTEM_PROMPT` pour lui interdire de les utiliser directement — moins strict mais fonctionnellement équivalent en pratique.

### 3.3 `agent/harness/runner.py`

Ajout d'une entrée dans les deux dicts existants, aucun changement structurel (le passthrough `**preset_kwargs` de la Phase 2b suffit déjà) :

```python
_PRESET_BUILDERS = {..., "ask": build_ask_options}
_MOCK_ANSWERS = {..., "ask": "Réponse mock VIGIE (routeur). Délégation simulée (mock)."}
```

### 3.4 Câblage des appelants

- **`agent/routes/ask.py`** : remplace `from agent.services.agent_loop import agent_loop` + `agent_loop(req.question, tenant_id=tenant_id, endpoint="ask")` par `from agent.harness.runner import run_agent` + `run_agent("ask", req.question, tenant_id=tenant_id, endpoint="ask")`. Contrat HTTP inchangé (mêmes `AskRequest`/réponse `{"answer": ..., "tenant_id": ...}`).
- **`agent/mcp/server.py::explain_anomaly`** (lignes 113-128) : même substitution, `endpoint="mcp/explain_anomaly"` conservé. `agent_loop` n'est alors plus importé dans ce fichier.
- **`agent/services/agent_loop.py`, `agent/routes/report.py`, `agent/services/alerting.py`** : inchangés.

## 4. Flux de données

Avant : `POST /ask` → `agent_loop()` → `run_agent("diagnostic", ...)` → agent unique avec accès Loki/Prometheus/Tempo uniquement, aucune donnée métier accessible.
Après : `POST /ask` → `run_agent("ask", ...)` → agent racine (aucun outil propre) lit la question → délègue via l'outil `Agent` à `diagnostic-investigator` (Loki/Prometheus/Tempo) ou `business-analyst` (KPIs/taxonomie), potentiellement les deux en séquence → synthèse retournée comme `result_message.result`, identique en forme à aujourd'hui (`run_agent()` ne change pas sa signature de retour : toujours une `str`).

Pas de changement pour `report/daily`/alerting : ils continuent d'appeler `agent_loop()` → preset `diagnostic` directement, sans passer par le routeur.

## 5. Gestion d'erreurs

Aucun changement au traitement d'erreur de `run_agent()` (budget épuisé, `is_error`, exception CLI) — le preset `"ask"` suit exactement le même chemin que les presets existants, `run_agent()` reste agnostique du preset appelé. Un cas nouveau à considérer : si l'agent racine ne délègue à aucun sous-agent (répond directement malgré `ASK_SYSTEM_PROMPT` et `disallowed_tools`), le comportement reste correct (une réponse est quand même retournée) mais dégradé — pas un échec dur, pas de code de gestion d'erreur spécifique nécessaire, à surveiller via l'audit log (`make_audit_hook`) lors de la vérification manuelle (§6).

## 6. Tests

- `tests/unit/test_biz_server.py` (nouveau) : `query_business_kpis` (taxonomie vide → `{}`, taxonomie avec événements → comptage correct via `run_query_loki` monkeypatché), `query_taxonomy` (taxonomie absente vs présente).
- `tests/unit/test_harness_options.py` (étendu, ou fichier équivalent s'il existe déjà pour `options.py`) : `build_ask_options` — `agents` contient exactement `diagnostic-investigator`/`business-analyst` avec des listes `tools` disjointes et correctes, `mcp_servers` contient `vigie-obs`+`vigie-biz`, `disallowed_tools` couvre les 5 noms, 2 `HookMatcher` par phase (`PreToolUse`/`PostToolUse`).
- `tests/unit/test_harness_runner.py` (étendu) : mock preset `"ask"` retourne la fixture ; dispatch vers `build_ask_options` (même pattern que les tests existants pour triage/taxonomy/discovery).
- `tests/integration/test_api.py`, `tests/isolation/test_non_fuite.py` : aucun changement de contrat attendu (mode mock) — à faire tourner pour confirmer l'absence de régression, pas de nouveau cas requis pour cette phase.
- **Délégation réelle** (question technique → `diagnostic-investigator`, question métier → `business-analyst`) : décision du LLM, non déterministe, donc pas testable de façon fiable par une assertion automatisée en CI. Vérification prévue en **exécution manuelle** (`VIGIE_MOCK_LLM=0`, une question technique et une question métier, lecture des logs d'audit — `make_audit_hook` journalise chaque appel outil, donc quel sous-agent a été invoqué) plutôt qu'un test automatisé strict. Cohérent avec l'absence de tout test de ce type ailleurs dans le repo pour un comportement de décision LLM (le triage/la taxonomie ne sont testés qu'en mode mock).

## 7. Hors périmètre de cette phase

- **Support de session multi-tours sur `/ask`** : `/ask` reste stateless, chaque appel repart de zéro comme aujourd'hui. Le SDK expose `resume`/`session_id` sur `ClaudeAgentOptions` mais rien n'est branché dessus dans cette phase — décision explicite pour garder le contrat HTTP de `/ask` inchangé et éviter d'élargir le périmètre (stockage de session, expiration, tests de fuite inter-tenant sur les sessions).
- **`agent/mcp/server.py` en vrai serveur MCP protocolaire** (§3.5 du design cible) : c'est la Phase 4, distincte, non commencée.
- **`report/daily` et le cycle d'alerting** : restent sur `agent_loop()`/preset `diagnostic` direct, aucune migration vers l'agent `ask`.
- **Accès direct de `business-analyst` à Loki brut** : écarté — `query_business_kpis` encapsule l'agrégation, le sous-agent ne voit jamais de LogQL.
