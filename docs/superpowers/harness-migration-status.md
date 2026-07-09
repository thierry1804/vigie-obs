# Migration VIGIE vers le Claude Agent SDK — État d'avancement

Document de suivi, pas une spec. Sert de point d'entrée pour reprendre le travail dans une nouvelle session/IA sans avoir à relire toute la conversation d'origine. Mis à jour au fil des phases — dernière mise à jour : 2026-07-09, après la Phase 3.

## 1. Contexte en une minute

VIGIE (`agent/`) est un agent d'observabilité self-hosted. Il appelait l'API Anthropic à la main (`agent/services/llm_client.py`, boucle de tours manuelle) pour 4 usages : diagnostic conversationnel (`/ask`), triage d'alertes, apprentissage de taxonomie métier, découverte automatique de sources de logs (`discovery`). Objectif de la migration : remplacer ce socle par le **Claude Agent SDK** comme harness (`agent/harness/`), avec une architecture multi-agents spécialisés plutôt qu'un agent unique.

Design cible complet (les 5 étapes prévues, dont seules 1 à 3 sont faites à ce jour) : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md`.

**Méthode de travail** (à reproduire pour la suite) : chaque étape suit le cycle superpowers complet — `brainstorming` (explore le code réel, pose des questions, produit un design) → `writing-plans` (plan TDD détaillé, tâches bite-sized) → `subagent-driven-development` (un sous-agent implémenteur + un sous-agent reviewer par tâche, dans un worktree git isolé) → `finishing-a-development-branch` (merge). Les designs et plans sont commités dans `docs/superpowers/specs/` et `docs/superpowers/plans/` avant toute implémentation — **toujours les relire en premier**, ce document ne fait que les résumer et pointer dessus.

## 2. Avancement par phase

| Phase | Statut | Design | Plan | Résultat |
|---|---|---|---|---|
| Phase 1 — agent diagnostic | ✅ Mergée | [design](specs/2026-07-07-architecture-agentique-harness-design.md) | [plan](plans/2026-07-07-harness-diagnostic-migration.md) | `agent_loop()` → `run_agent("diagnostic", ...)` |
| Phase 2a — triage + taxonomie | ✅ Mergée | [design](specs/2026-07-08-harness-phase2a-triage-taxonomy-design.md) | [plan](plans/2026-07-08-harness-phase2a-triage-taxonomy.md) | `triage_alert()`/`propose_taxonomy()` migrés |
| Phase 2b — discovery | ✅ Mergée | [design](specs/2026-07-09-harness-phase2b-discovery-design.md) | [plan](plans/2026-07-09-harness-phase2b-discovery.md) | `infer_formats()`/`run_discovery()` migrés |
| Phase 3 — agent orchestrateur `ask` | ✅ Mergée | [design](specs/2026-07-09-harness-phase3-ask-orchestrator-design.md) | [plan](plans/2026-07-09-harness-phase3-ask-orchestrator.md) | `build_ask_options()` (routeur + 2 sous-agents) ; `/ask`/`explain_anomaly` migrés |
| Phase 4 — vrai serveur MCP externe | ⬜ Non commencée | — | — | Voir §5 |

Suite de tests : 24 (avant Phase 1) → 49 (après Phase 1) → 64 (après Phase 2a) → 77 (après Phase 2b) → 94 (après Phase 3), toutes vertes sur `master`.

## 3. Architecture actuelle (post Phase 3)

```
agent/harness/
  hooks.py    make_budget_guard_hook(tenant_id)   — PreToolUse, coupe si budget épuisé
              make_tenant_scope_hook(tenant_id)    — PreToolUse, verrouille les requêtes LogQL
                                                      sur le tenant courant (auto-injection si
                                                      absent, deny si tenant différent explicite)
              make_audit_hook(tenant_id)           — PostToolUse, journalise chaque appel d'outil
              anonymize_hook                        — PostToolUse, rédige les emails (générique,
                                                      pas de factory, pas d'état par tenant)
  options.py  build_diagnostic_options(tenant_id, system_prompt=None)
              build_triage_options(tenant_id, system_prompt=None)
              build_taxonomy_options(tenant_id, system_prompt=None)
              build_discovery_options(tenant_id, system_prompt=None, *, report)  — report obligatoire
  runner.py   run_agent(preset, user_message, tenant_id="default", endpoint="ask",
                         system_prompt=None, **preset_kwargs) -> str
              — point d'entrée UNIQUE vers le LLM pour les 4 presets. Court-circuite sur
                VIGIE_MOCK_LLM=1 (réponse fixture par preset, _MOCK_ANSWERS). Sinon : vérifie
                le budget en amont (avant tout outil), construit les options via
                _PRESET_BUILDERS[preset](...), itère query(), gère erreurs/is_error, enregistre
                l'usage.

agent/tools/
  mcp_server.py      build_obs_mcp_server(tenant_id) — "vigie-obs" : query_loki, query_prometheus,
                      query_traces (utilisé par diagnostic + taxonomy + sous-agent
                      diagnostic-investigator du preset ask)
  biz_server.py      NOUVEAU (Phase 3) build_biz_mcp_server(tenant_id) — "vigie-biz" :
                      query_business_kpis, query_taxonomy. Isolé de vigie-obs pour ne pas
                      exposer ces 2 outils aux presets diagnostic/taxonomy existants — utilisé
                      uniquement par le sous-agent business-analyst du preset ask.
  fs_scan_server.py  build_fs_scan_mcp_server(report) — "vigie-fs" : sample_lines, set_framework_hint
                      (utilisé par discovery ; borné à un index dans report.log_sources, jamais
                      de chemin libre)
  registry.py        MORT — remplacé par mcp_server.py, plus aucune référence dans le code.
                      Pas encore supprimé (voir §6).
  loki.py / prometheus.py / traces.py — fonctions HTTP sous-jacentes, inchangées

agent/harness/
  options.py  + build_ask_options(tenant_id, system_prompt=None) — NOUVEAU (Phase 3). Agent
              racine routeur pur : disallowed_tools bloque son accès direct aux 5 outils MCP
              (vigie-obs + vigie-biz), agents={} définit 2 AgentDefinition sous-agents
              (diagnostic-investigator : 3 outils vigie-obs, MAX_TOOL_TURNS ; business-analyst :
              2 outils vigie-biz, MODEL_TRIAGE, 3 tours). Vérifié par un run réel (non mocké) :
              disallowed_tools au niveau racine n'empêche PAS les sous-agents d'utiliser leurs
              propres outils — les deux mécanismes sont indépendants (fait confirmé, plus une
              hypothèse — voir §4.9).
  runner.py   _PRESET_BUILDERS/_MOCK_ANSWERS gagnent l'entrée "ask" (build_ask_options).

agent/services/
  agent_loop.py   agent_loop() — wrapper fin sur run_agent("diagnostic", ...), signature inchangée.
                  Reste utilisé UNIQUEMENT par report/daily et le cycle d'alerting (déterministes,
                  pas de délégation agent-à-agent nécessaire — décision explicite Phase 3 §7).
  triage.py       triage_alert() — devenu async, appelle run_agent("triage", ...)
  taxonomy.py     propose_taxonomy() — agentique : l'agent interroge query_loki lui-même
                  (sample_logs()/anonymize() Python supprimés). validate/apply/diff/generate_vrl/
                  load_taxonomy inchangés. load_taxonomy() est aussi la source du nouvel outil
                  query_taxonomy (Phase 3).
  discovery.py    infer_formats()/run_discovery() — devenus async. L'agent classifie réellement
                  via set_framework_hint (corrige un bug : avant, la réponse LLM était calculée
                  et jamais utilisée). discover_target() (scan Python : chemins, ports, docker)
                  reste inchangé — décision explicite, ces primitives n'ont pas de valeur
                  itérative. generate_vector_config()/diff_config() inchangés.
  alerting.py     process_alert() fait maintenant `await triage_alert(...)`
  llm_client.py   PAS mort : agent/harness/runner.py importe encore `_mock_enabled()` d'ici.
                  `create_message()` en revanche n'est plus appelé nulle part (voir §6).

agent/routes/ask.py         POST /ask appelle maintenant run_agent("ask", ...) au lieu
                             d'agent_loop() — contrat HTTP inchangé.
agent/mcp/server.py          explain_anomaly appelle maintenant run_agent("ask", ...,
                              endpoint="mcp/explain_anomaly") — contrat HTTP inchangé.
```

## 4. Faits techniques confirmés (à ne pas re-découvrir)

Tous confirmés par sondage réel du SDK installé (jamais supposés depuis la doc/mémoire) :

1. **`claude-agent-sdk` pilote le CLI `claude` en sous-processus**, pas d'appel HTTP direct à l'API Anthropic. Implique Node.js + `npm install -g @anthropic-ai/claude-code` dans l'image Docker de l'agent (déjà fait, `agent/Dockerfile`).
2. **`permission_mode="bypassPermissions"` est obligatoire** sur tout preset qui expose des outils, dans un service headless — sans ça, l'outil est bloqué par défaut en session non-interactive (nommage correct, mais jamais exécuté).
3. Convention de nommage des outils MCP vus par le modèle : `mcp__<nom_serveur>__<nom_outil>`.
4. **`PostToolUseHookInput["tool_response"]` est une liste** de blocs de contenu (`[{"type": "text", "text": "..."}]`), jamais un dict `{"content": [...]}`. **`updatedMCPToolOutput` doit avoir exactement la même forme** — un dict casse l'appel d'outil côté CLI (erreur JS `"e.reduce is not a function"`).
5. `StopHookInput` ne transporte aucune donnée d'usage token — l'usage vient de `ResultMessage.usage` (message final de l'itérateur `query()`).
6. `ResultMessage.is_error`/`errors` doivent être vérifiés explicitement — sans ça, un échec CLI/API est traité comme un succès et son texte (vide ou une erreur brute) est retourné comme si c'était une vraie réponse.
7. **Le budget doit être vérifié en amont dans `run_agent()`**, pas seulement via un hook `PreToolUse` — sinon un preset qui ne finit par n'appeler aucun outil (réponse directe du modèle) contourne totalement la limite de budget.
8. Le hook de scoping tenant doit **corriger** (auto-injecter le tenant via `updatedInput`) plutôt que juste refuser quand le tenant est absent — refuser casserait l'usage normal ; ne rien faire laisserait une vraie fuite inter-tenant (le bug pré-existant dans `agent/tools/loki.py` ne scope pas les requêtes LogQL commençant déjà par `{`).
9. **`ClaudeAgentOptions.disallowed_tools` (racine) et `AgentDefinition.tools` (par sous-agent) sont indépendants** — confirmé par un run réel non mocké (Phase 3) : un agent racine avec les 5 noms d'outils MCP dans `disallowed_tools` délègue correctement à un sous-agent qui, lui, utilise ses propres outils autorisés sans blocage. Permet de construire un agent "routeur pur" (jamais d'appel outil direct) sans que ça n'affecte les sous-agents qu'il invoque.
10. **Import circulaire réel** entre `agent/harness/options.py` → un module d'outils qui importe `agent/services/taxonomy.py` → `agent/harness/runner.py` → `agent/harness/options.py` (car `taxonomy.py` importe `run_agent` depuis `runner.py`, qui importe tous les builders d'`options.py`). Se manifeste dès qu'un nouveau builder d'`options.py` importe en top-level un module d'outils qui dépend (même transitivement) de `taxonomy.py`. Contournement appliqué : import différé (dans le corps de la fonction) plutôt qu'en tête de fichier — safe car le module appelant a fini son propre chargement avant qu'aucune fonction ne soit invoquée.

## 5. Ce qui reste (Phase 4+, non planifié en détail)

Du design cible original (§3.5 de `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md`) :

- **Vrai serveur MCP externe** (`agent/mcp/server.py`) : aujourd'hui du REST qui imite des noms d'outils MCP, pas le vrai protocole JSON-RPC. À remplacer par un transport Streamable HTTP conforme, consommable par de vrais agents externes (proto-factory).

Pas de design/plan écrit — à faire via le cycle complet (brainstorming d'abord) avant d'implémenter.

Hors design cible original, envisagé mais explicitement différé lors du brainstorming Phase 3 (voir `docs/superpowers/specs/2026-07-09-harness-phase3-ask-orchestrator-design.md` §7) :

- **Support de session multi-tours sur `/ask`** : reste stateless par choix, chaque appel repart de zéro. Nécessiterait `session_id`/`resume` du SDK, un stockage de session et des tests de fuite inter-tenant dédiés.

## 6. Nettoyages identifiés mais non faits

Trouvés pendant les revues, délibérément non traités pour rester dans le périmètre de chaque tâche :

- **`agent/tools/registry.py`** : mort (aucune référence), à supprimer. Trivial.
- **`agent/services/llm_client.py`** : `create_message()` n'est plus appelé nulle part, mais `_mock_enabled()` est encore importé par `agent/harness/runner.py`. Pour supprimer ce fichier, déplacer `_mock_enabled()` ailleurs d'abord (ex. dans `runner.py` directement, ou un petit helper de config partagé).
- **Duplication entre `build_diagnostic_options`/`build_taxonomy_options`/`build_discovery_options`** (`agent/harness/options.py`) : chacun reconstruit un dict `hooks={"PreToolUse": [...], "PostToolUse": [...]}` très similaire. Signalé dès la revue finale de la Phase 2a, encore plus vrai avec 3 presets à outils maintenant — une fonction `_standard_tool_hooks(tenant_id)` réduirait la répétition. Vaut le coup de le faire avant la Phase 3 (l'agent orchestrateur ajoutera probablement 1-2 presets de plus).
- **`framework_hint` (Phase 2b) ne remonte pas jusqu'à `proposed_config`** : `config/templates/vector.toml.j2` ne référence jamais le champ `framework`. La classification de l'agent est donc correcte dans `report` mais sans effet sur la config Vector générée. Pré-existant (le template ignorait déjà ce champ avant la Phase 2b), pas une régression, mais la valeur de la Phase 2b pour la config Vector elle-même est actuellement nulle — seul le rapport de diagnostic en bénéficie. À corriger si on veut que la classification serve réellement à la génération de config.
- **Asymétrie d'anonymisation (Phase 2b)** : les échantillons initiaux de `discover_target()` sont envoyés au modèle dans le prompt (pas via un outil), donc `anonymize_hook` (PostToolUse) ne les couvre pas — seuls les ré-échantillonnages via l'outil `sample_lines` sont rédigés. Pas une régression (comportement identique à avant la migration), mais une incohérence si on tient à la garantie de rédaction partout.
- **`query_business_kpis` (Phase 3) plafonne silencieusement le comptage** : `run_query_loki(..., limit=5, ...)` par événement, donc tout événement avec plus de 5 lignes dans la fenêtre est sous-évalué ; une réponse d'erreur HTTP de Loki est aussi comptée comme 1 occurrence plutôt que remontée comme erreur (aucun test ne couvre ce chemin). Champ de sortie honnêtement nommé `sample_lines` (pas `count`), mais la description de l'outil dit "Compte les occurrences" — décalage à corriger si la précision des KPIs devient importante.
- **Listes de noms d'outils partagées par référence (Phase 3)** : `_ASK_OBS_TOOL_NAMES`/`_ASK_BIZ_TOOL_NAMES` (`agent/harness/options.py`) sont des listes module-level passées telles quelles dans chaque `AgentDefinition.tools=` à chaque appel de `build_ask_options()` — partagées entre tous les tenants/requêtes. Inoffensif aujourd'hui (rien ne les mute), mais une copie défensive (`list(...)`) éliminerait le risque latent.
- **Import différé dans `build_ask_options()` (Phase 3)** : contournement d'un cycle d'import réel (voir §4.10) via un import en corps de fonction plutôt qu'en tête de fichier — seul cas de ce genre dans `options.py`. Fonctionnellement correct et déjà vérifié, mais signale une dépendance `taxonomy.py → runner.py` pré-existante et un peu tendue (une commande "métier" dépend du harness LLM) ; à garder à l'œil si un futur builder retombe dans le même cycle.

## 7. Comment reprendre

1. Lire ce document en entier (5 minutes).
2. Pour continuer la migration (Phase 4) : lancer le skill `superpowers:brainstorming` sur le vrai serveur MCP externe (`agent/mcp/server.py` en transport Streamable HTTP conforme, §5) — dernier morceau du design cible original.
3. Pour un nettoyage (§6) : ce sont des tâches suffisamment petites pour sauter le cycle complet brainstorming→plan si l'utilisateur le confirme — mais toujours passer par `superpowers:writing-plans` au minimum si le nettoyage touche plus d'un fichier (le cas de `llm_client.py`).
4. `git log --oneline` sur `master` donne l'historique complet et fiable des commits par tâche — chaque message de commit de fix explique le bug trouvé et corrigé, souvent plus précis que ce résumé.
