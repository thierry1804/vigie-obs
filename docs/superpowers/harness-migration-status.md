# Migration VIGIE vers le Claude Agent SDK — État d'avancement

Document de suivi, pas une spec. Sert de point d'entrée pour reprendre le travail dans une nouvelle session/IA sans avoir à relire toute la conversation d'origine. Mis à jour au fil des phases — dernière mise à jour : 2026-07-09, après la Phase 4.

## 1. Contexte en une minute

VIGIE (`agent/`) est un agent d'observabilité self-hosted. Il appelait l'API Anthropic à la main (`agent/services/llm_client.py`, boucle de tours manuelle) pour 4 usages : diagnostic conversationnel (`/ask`), triage d'alertes, apprentissage de taxonomie métier, découverte automatique de sources de logs (`discovery`). Objectif de la migration : remplacer ce socle par le **Claude Agent SDK** comme harness (`agent/harness/`), avec une architecture multi-agents spécialisés plutôt qu'un agent unique.

Design cible complet (les 5 étapes prévues, toutes faites à ce jour) : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md`.

**Méthode de travail** (à reproduire pour la suite) : chaque étape suit le cycle superpowers complet — `brainstorming` (explore le code réel, pose des questions, produit un design) → `writing-plans` (plan TDD détaillé, tâches bite-sized) → `subagent-driven-development` (un sous-agent implémenteur + un sous-agent reviewer par tâche, dans un worktree git isolé) → `finishing-a-development-branch` (merge). Les designs et plans sont commités dans `docs/superpowers/specs/` et `docs/superpowers/plans/` avant toute implémentation — **toujours les relire en premier**, ce document ne fait que les résumer et pointer dessus.

## 2. Avancement par phase

| Phase | Statut | Design | Plan | Résultat |
|---|---|---|---|---|
| Phase 1 — agent diagnostic | ✅ Mergée | [design](specs/2026-07-07-architecture-agentique-harness-design.md) | [plan](plans/2026-07-07-harness-diagnostic-migration.md) | `agent_loop()` → `run_agent("diagnostic", ...)` |
| Phase 2a — triage + taxonomie | ✅ Mergée | [design](specs/2026-07-08-harness-phase2a-triage-taxonomy-design.md) | [plan](plans/2026-07-08-harness-phase2a-triage-taxonomy.md) | `triage_alert()`/`propose_taxonomy()` migrés |
| Phase 2b — discovery | ✅ Mergée | [design](specs/2026-07-09-harness-phase2b-discovery-design.md) | [plan](plans/2026-07-09-harness-phase2b-discovery.md) | `infer_formats()`/`run_discovery()` migrés |
| Phase 3 — agent orchestrateur `ask` | ✅ Mergée | [design](specs/2026-07-09-harness-phase3-ask-orchestrator-design.md) | [plan](plans/2026-07-09-harness-phase3-ask-orchestrator.md) | `build_ask_options()` (routeur + 2 sous-agents) ; `/ask`/`explain_anomaly` migrés |
| Phase 4 — vrai serveur MCP externe | ✅ Mergée | [design](specs/2026-07-09-harness-phase4-mcp-external-design.md) | [plan](plans/2026-07-09-harness-phase4-mcp-external.md) | `agent/mcp/server.py` → vrai transport MCP (SDK `mcp`, `FastMCP`, Streamable HTTP) |

Suite de tests : 24 (avant Phase 1) → 49 (après Phase 1) → 64 (après Phase 2a) → 77 (après Phase 2b) → 94 (après Phase 3) → 97 (après Phase 4), toutes vertes sur `master`.

Avec la Phase 4, les 5 étapes du design cible original (`docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` §3.1-3.5) sont toutes mergées. Le reste (§5 ci-dessous) est hors périmètre de ce design initial.

## 3. Architecture actuelle (post Phase 4)

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

agent/mcp/
  auth.py     NOUVEAU (Phase 4) VigieTokenVerifier(TokenVerifier) — verify_token(token) cherche
              Tenant.mcp_token en base, renvoie un AccessToken (claims={"tenant_id": ...},
              scopes=["mcp"]) ou None. Même logique d'auth qu'avant, portée par l'interface
              TokenVerifier du SDK mcp au lieu d'une dépendance FastAPI custom.
  tools.py    NOUVEAU (Phase 4) logique des 4 outils MCP externes, indépendante du transport :
              get_project_health, query_incidents, get_business_kpis, explain_anomaly
              (délègue à run_agent("ask", ..., endpoint="mcp/explain_anomaly")). Le tenant
              courant vient de mcp.server.auth.middleware.auth_context.get_access_token()
              (contextvar posée par le middleware d'auth avant l'appel du handler), plus de
              header X-Tenant-ID ni de paramètre tenant_id explicite sur les outils.
  server.py   RÉÉCRIT (Phase 4) build_mcp_server() — factory FastMCP (jamais de singleton,
              voir fait §4.11) : token_verifier=VigieTokenVerifier(), auth=AuthSettings(
              issuer_url=..., resource_server_url=None) (pas d'autorité OAuth réelle — juste
              pour activer le middleware d'auth qui consomme token_verifier, voir fait §4.12),
              streamable_http_path="/". register_tools() y attache les 4 outils.
```

`agent/main.py::lifespan()` construit une instance `build_mcp_server()` fraîche à chaque cycle,
peuple `app.state.mcp_app = mcp_server.streamable_http_app()` et entre
`mcp_server.session_manager.run()` avant le `yield` (obligatoire, voir fait §4.11). Un petit
wrapper ASGI (`_mcp_asgi`, délègue à `scope["app"].state.mcp_app`) est monté une seule fois et
statiquement sur `app.mount("/mcp", _mcp_asgi)` — un `Mount` Starlette classique ne peut pas
référencer une app reconstruite à chaque lifespan, d'où l'indirection par `app.state`.

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
11. **`StreamableHTTPSessionManager.run()` (Phase 4) ne peut être entré qu'une seule fois par instance**, jamais réutilisable après sortie (contrainte documentée du SDK `mcp`). Un singleton `mcp_server`/`mcp_app` construit une fois au niveau module casse dès qu'un deuxième cycle de `lifespan` a lieu dans le même process (le cas de tout test qui recrée un `TestClient(app)`). D'où `build_mcp_server()` en factory, appelée à l'intérieur du `lifespan` d'`agent/main.py`, une instance fraîche par cycle — sans impact en production (un seul cycle de toute façon).
12. **Sans `auth=AuthSettings(...)` fourni à `FastMCP`, `token_verifier` est inopérant** (Phase 4) : `streamable_http_app()` ne construit les middlewares d'authentification que si `self.settings.auth` est vérité — les deux doivent donc être fournis ensemble, même si VIGIE ne fait office d'aucune autorité OAuth réelle (`resource_server_url=None` désactive les routes `.well-known/oauth-protected-resource`, `issuer_url` doit juste être une URL valide sans jamais être lue par du code tant qu'aucun `auth_server_provider` n'est configuré).

## 5. Ce qui reste (hors design cible original, non planifié en détail)

Les 5 étapes du design cible original (`docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` §3.1-3.5) sont toutes mergées depuis la Phase 4. Il ne reste plus rien de planifié à ce niveau — items envisagés mais explicitement différés lors de brainstormings antérieurs :

- **Support de session multi-tours sur `/ask`** (différé Phase 3, voir `docs/superpowers/specs/2026-07-09-harness-phase3-ask-orchestrator-design.md` §7) : reste stateless par choix, chaque appel repart de zéro. Nécessiterait `session_id`/`resume` du SDK, un stockage de session et des tests de fuite inter-tenant dédiés.
- **Client MCP externe réel** (aucun n'existe encore, même après la Phase 4) : le vrai protocole est maintenant servi (`docs/mcp-integration.md`), mais aucun agent tiers ne s'y connecte en pratique — seuls les tests d'intégration du dépôt (`tests/integration/test_mcp_protocol.py`) jouent ce rôle. À surveiller si proto-factory ou un autre consommateur externe se raccorde un jour (contrat d'auth/format de réponse à valider en conditions réelles).

Pour toute nouvelle initiative hors de ce périmètre : repasser par le cycle complet (`brainstorming` d'abord) avant d'écrire du code.

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
- **`get_business_kpis` (Phase 4, ex-`query_business_kpis` Phase 3) hérite du même plafond silencieux** : `run_query_loki(..., limit=5, ...)` par événement, toujours pas corrigé lors de la réécriture Phase 4 (hors périmètre de cette tâche, transport uniquement). Voir l'entrée Phase 3 ci-dessus, toujours valable telle quelle.
- **`agent/mcp/tools.py::explain_anomaly` recharge le preset `ask` complet par question** (Phase 4) : chaque appel MCP externe déclenche un run agentique multi-tours (routeur + sous-agents), donc un coût/latence par appel largement supérieur à un simple outil MCP — c'est le comportement voulu (hérité de la Phase 3), mais un consommateur externe non prévenu pourrait être surpris par la latence.

## 7. Comment reprendre

1. Lire ce document en entier (5 minutes).
2. Les 5 étapes du design cible original sont toutes mergées (Phases 1 à 4) — pour toute nouvelle initiative, voir §5 (items différés) ou repartir d'un besoin utilisateur neuf via `superpowers:brainstorming`.
3. Pour un nettoyage (§6) : ce sont des tâches suffisamment petites pour sauter le cycle complet brainstorming→plan si l'utilisateur le confirme — mais toujours passer par `superpowers:writing-plans` au minimum si le nettoyage touche plus d'un fichier (le cas de `llm_client.py`).
4. `git log --oneline` sur `master` donne l'historique complet et fiable des commits par tâche — chaque message de commit de fix explique le bug trouvé et corrigé, souvent plus précis que ce résumé.
