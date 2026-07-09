# VIGIE — Harness Phase 2b : migration + enrichissement discovery

Statut : Design validé | 2026-07-09
Réfère à : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` (design cible harness/multi-agents), `docs/superpowers/specs/2026-07-08-harness-phase2a-triage-taxonomy-design.md` (Phase 2a, déjà mergée), `docs/superpowers/plans/2026-07-07-harness-diagnostic-migration.md` (Phase 1, déjà mergée)

## 1. Contexte et motivation

La Phase 1 a migré l'agent diagnostic vers le Claude Agent SDK. La Phase 2a a migré `triage.py` (simple) et `taxonomy.py` (enrichi, agentique) — `discovery.py` avait été explicitement reporté à cette phase.

**Constat fait en explorant le code réel** : `infer_formats()` (dans `agent/services/discovery.py`) appelle bien le LLM aujourd'hui (`create_message`), mais **n'utilise jamais sa réponse** — `response.content` n'est ni parsé ni consulté. Le seul `framework_hint` réellement appliqué vient d'une heuristique Python fixe (`sample_lines[0].startswith("{")`). L'appel LLM actuel coûte des tokens sans effet fonctionnel réel. Cette migration corrige ce bug : la conclusion de l'agent pilotera réellement la classification.

**Constat technique qui a réduit le périmètre de l'enrichissement** : sur les 4 primitives de scan de `discovery/scanner.py` (`scan_log_paths`, `sample_lines`, `scan_ports`, `scan_docker`), trois (`scan_log_paths`, `scan_ports`, `scan_docker`) n'ont pas de valeur itérative réelle — `scan_log_paths` fait déjà un `os.walk()` récursif complet en un seul appel (le rappeler ne renvoie rien de nouveau), et `scan_ports`/`scan_docker` sont des vérifications à liste fixe sans paramètre variable. Les rendre agentiques ajouterait de la complexité (et un mécanisme de confinement de chemin à sécuriser, puisque le scan peut tourner sur un système en production chez un client) sans bénéfice fonctionnel. Seuls `sample_lines` (réechantillonnage borné à une source déjà découverte) et une nouvelle classification (`set_framework_hint`, qui corrige le bug ci-dessus) ont une vraie valeur d'agentivité.

**Décision retenue avec l'utilisateur** : `scan_log_paths`/`scan_ports`/`scan_docker` restent un pré-passage Python déterministe inchangé (comme en Phase 1/2a, la collecte low-level reste hors du champ du LLM — invariant produit "le LLM n'analyse jamais le flux brut"). Seuls `sample_lines` et `set_framework_hint` deviennent des outils agentiques, bornés à des sources déjà découvertes par Python — **aucune entrée de chemin libre nulle part**, donc aucun mécanisme de confinement de chemin à construire.

## 2. Architecture

```
discovery/scanner.py         — INCHANGÉ (scan_log_paths, scan_ports, scan_docker
                                 restent un pré-passage Python déterministe)

agent/tools/fs_scan_server.py — NOUVEAU, in-process, lié à un DiscoveryReport
                                 (pas à un tenant_id) :
  - sample_lines(source_index, max_lines)   → ré-échantillonne une source déjà
                                                trouvée, aucun chemin libre en entrée
  - set_framework_hint(source_index, framework) → nouvel outil, corrige le bug :
                                                     la conclusion de l'agent est
                                                     réellement appliquée

agent/harness/
  options.py  + build_discovery_options(tenant_id, system_prompt=None, *, report)
                model=MODEL_TRIAGE, max_turns=6, mcp_servers={"vigie-fs": ...}
                hooks: PreToolUse=[budget_guard] (pas de tenant_scope — aucune
                donnée scopée par tenant en jeu) ; PostToolUse=[audit, anonymize_hook]
                (cohérent avec la Phase 2a : les échantillons de logs peuvent
                contenir des emails clients, même règle appliquée partout)
  runner.py   ~ run_agent() gagne **preset_kwargs (passthrough générique vers le
                builder du preset) — nécessaire car discovery a besoin de `report`
                en plus de tenant_id, contrairement aux presets existants

agent/services/discovery.py
  ~ infer_formats() devient async, agentique : construit un prompt décrivant les
    sources déjà trouvées, appelle run_agent("discovery", ..., report=report) ;
    les outils mutent `report` en place, donc au retour le report reflète déjà
    les conclusions de l'agent — pas besoin de parser une réponse finale
  ~ run_discovery() devient async ; saute l'appel agent si report.log_sources
    est vide (rien à classifier, budget non consommé pour rien)

agent/routes/discover.py  ~ POST /discover ajoute await (corrige au passage un
                             blocage synchrone dans un handler async, même classe
                             de bug que celui corrigé en Phase 1 sur agent_loop)
cli/__main__.py            ~ commande discover enveloppée dans asyncio.run(...)
                             (même pattern que la commande taxonomy déjà async)
```

## 3. Composants

### 3.1 `agent/tools/fs_scan_server.py`

`build_discovery_tools(report: DiscoveryReport) -> list[SdkMcpTool]` et `build_fs_scan_mcp_server(report: DiscoveryReport) -> McpSdkServerConfig`, sur le modèle de `agent/tools/mcp_server.py`. Les deux outils mutent le `DiscoveryReport` passé par closure — pas d'état global, une instance fraîche par appel de `run_discovery()`.

- `sample_lines(source_index: int, max_lines: int = 20)` : valide `0 <= source_index < len(report.log_sources)` (sinon `is_error: True`), appelle `discovery.scanner.sample_lines()` sur la source correspondante, renvoie les lignes échantillonnées.
- `set_framework_hint(source_index: int, framework: str)` : même validation, écrit `report.log_sources[source_index].framework_hint = framework`.

### 3.2 Preset `discovery`

`build_discovery_options(tenant_id: str, system_prompt: str | None = None, *, report: DiscoveryReport) -> ClaudeAgentOptions` — `report` est **obligatoire, keyword-only** : sans lui, l'agent n'a structurellement rien à faire, donc un appel sans `report` doit échouer immédiatement (`TypeError`) plutôt que produire un no-op silencieux. `model=MODEL_TRIAGE` (Haiku, cohérent avec le choix actuel), `max_turns=6`, `mcp_servers={"vigie-fs": build_fs_scan_mcp_server(report)}`, `permission_mode="bypassPermissions"` (requis pour tout preset avec outils, confirmé Phase 1). Hooks : `PreToolUse=[make_budget_guard_hook(tenant_id)]` seul (pas de `tenant_scope` — les outils n'ont aucun paramètre `logql`/`promql`, le hook serait un no-op silencieux et trompeur s'il était inclus) ; `PostToolUse=[make_audit_hook(tenant_id), anonymize_hook]` (cohérent avec la Phase 2a : les échantillons de logs peuvent contenir des données personnelles).

### 3.3 `agent/harness/runner.py` — passthrough générique

```python
async def run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **preset_kwargs) -> str:
    ...
    options = _PRESET_BUILDERS[preset](tenant_id, system_prompt=system_prompt, **preset_kwargs)
    ...
```

Les presets existants (`diagnostic`, `triage`, `taxonomy`) n'utilisent jamais `**preset_kwargs` (aucun changement de comportement pour eux) ; seul `discovery` en a besoin pour recevoir `report`.

### 3.4 `agent/services/discovery.py`

`infer_formats(report: DiscoveryReport, tenant_id: str = "default") -> DiscoveryReport` devient `async def`, construit un prompt décrivant les sources déjà trouvées (via `report_to_json`) et instruit l'agent d'utiliser `sample_lines` si les échantillons sont ambigus puis d'appeler `set_framework_hint` pour **chaque** source avant de conclure. Appelle `run_agent("discovery", prompt, tenant_id=tenant_id, endpoint="discover", report=report)` — le texte retourné n'est pas utilisé (l'effet réel est dans les mutations d'outils), seul `report` (muté en place) est retourné.

`run_discovery(target, tenant_id="default", existing_config=None) -> dict` devient `async def`. Saute l'appel à `infer_formats()` si `report.log_sources` est vide après le scan déterministe.

## 4. Flux de données

Avant : `discover_target()` → `infer_formats()` (appel LLM sans effet réel) → `generate_vector_config()`.
Après : `discover_target()` (inchangé) → si `log_sources` vide, saut de l'agent → sinon `infer_formats()` agentique (rééchantillonnage optionnel + classification réelle via outils) → `generate_vector_config()` utilise les `framework_hint` réellement mis à jour.

## 5. Gestion d'erreurs

Budget épuisé ou erreur harness avant tout appel d'outil → aucun outil n'est appelé → `framework_hint` reste à sa valeur par défaut (heuristique Python déjà en place dans `scan_log_paths`/`_framework_hint`) → `generate_vector_config()` fonctionne quand même, juste sans raffinement LLM. Dégradation gracieuse par construction (VIGIE_Specs_Cible.md §5), aucun code spécial à ajouter. Une erreur en cours de route laisse un état partiel (certaines sources classifiées, d'autres non) — acceptable, pas de rollback nécessaire.

## 6. Tests

- `tests/unit/test_fs_scan_server.py` (nouveau) : les deux outils, y compris index hors bornes.
- `tests/unit/test_harness_options.py` (étendu) : preset `discovery` — 1 hook `PreToolUse`, 2 `PostToolUse`, `report` obligatoire.
- `tests/unit/test_harness_runner.py` (étendu) : mock preset `"discovery"` ; test du passthrough `**preset_kwargs` (un faux builder capture les kwargs reçus).
- `tests/integration/test_discovery_config.py` : devient async (`run_discovery` est maintenant une coroutine).
- Nouveau test unitaire pour `infer_formats()` : `run_agent` monkeypatché pour simuler un tour d'agent (appelle directement les fonctions du report comme le ferait un vrai appel d'outil), vérifie que la conclusion est bien appliquée — ferme la boucle sur le bug corrigé (aujourd'hui, aucun test ne couvre `infer_formats()`/`run_discovery()` au-delà d'assertions superficielles sur le template Vector).

## 7. Hors périmètre de cette phase

- Confinement de chemin pour une exploration filesystem libre : non nécessaire, aucun outil de cette phase n'accepte de chemin arbitraire.
- Retrait de `agent/services/llm_client.py` : devient possible maintenant que triage/taxonomy/discovery sont tous migrés — mais c'est un nettoyage séparé, pas inclus dans cette phase (mêmes fichiers, risque de conflit de scope si mélangé).
- Extension de `scan_ports`/`scan_docker`/`scan_log_paths` en outils agentiques : explicitement écarté (§1), aucune valeur itérative démontrée.
