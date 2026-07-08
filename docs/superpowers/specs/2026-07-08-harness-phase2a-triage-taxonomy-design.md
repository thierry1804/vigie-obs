# VIGIE — Harness Phase 2a : migration triage + enrichissement taxonomie

Statut : Design validé | 2026-07-08
Réfère à : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` (design cible harness/multi-agents), `docs/superpowers/plans/2026-07-07-harness-diagnostic-migration.md` (Phase 1, déjà mergée)

## 1. Contexte et motivation

La Phase 1 a migré l'agent diagnostic vers le Claude Agent SDK (`agent/harness/`). Ce document couvre l'étape suivante du plan de migration en 5 phases du design cible : `triage.py` et `taxonomy.py`.

**Constat fait en explorant le code réel** (contredit une hypothèse du design initial) : ni `triage.py` ni `taxonomy.py` ni `discovery.py` n'utilisent aujourd'hui de boucle agentique — ce sont des appels LLM **single-shot** (`create_message()` une fois). Le Python fait toute la collecte de données en amont (scan de fichiers pour discovery, `run_query_loki` direct pour taxonomy) et ne passe que le résultat au LLM. Autre constat : aucun des trois n'appelle `check_budget()` aujourd'hui — contrairement à l'agent diagnostic, ils n'ont aucune limite de budget LLM.

**Décisions prises avec l'utilisateur** :
- Ambition : enrichir en même temps que la migration (donner un vrai accès outils à taxonomie), pas une simple migration à l'identique.
- Budget : ajouter `check_budget()` aux trois services — mais **discovery est repoussé en intégralité à la Phase 2b** (nouveau serveur MCP `fs_scan` à construire, risque différent), pour ne pas modifier deux fois le même fichier. Cette phase (2a) ne touche que `triage.py` et `taxonomy.py`.
- Anonymisation : un hook `PostToolUse` générique de rédaction d'email, étendu à **tous** les presets (diagnostic inclus), pas seulement taxonomie — ferme au passage un vrai trou de confidentialité découvert en cours de route (l'agent diagnostic ne rédige aujourd'hui aucune donnée personnelle dans les logs qu'il lit).

## 2. Architecture

```
agent/harness/
  hooks.py     + anonymize_hook (PostToolUse générique, rédaction email —
                 fonction simple sans état, pas de factory par tenant)
  options.py   + build_triage_options(tenant_id)    → 0 outil, max_turns=1
               + build_taxonomy_options(tenant_id)   → vigie-obs (réutilisé
                 tel quel), max_turns=3, hooks: budget+tenant_scope (PreToolUse)
                 + audit+anonymize (PostToolUse)
               ~ build_diagnostic_options()           → + anonymize_hook ajouté
                 à sa liste PostToolUse existante
  runner.py    ~ _PRESET_BUILDERS + "triage"/"taxonomy"
               ~ court-circuit mock devient sensible au preset (JSON pour
                 triage, YAML pour taxonomy, texte pour diagnostic)

agent/services/triage.py    ~ triage_alert() appelle run_agent("triage", ...)
                              au lieu de create_message() ; TRIAGE_PROMPT
                              déménage dans harness/options.py
agent/services/taxonomy.py  ~ propose_taxonomy() devient agentique : l'agent
                              interroge lui-même query_loki (stream_type=
                              business) au lieu qu'un sample_logs() Python
                              pré-collecte les lignes. sample_logs()/
                              anonymize() Python retirés — la rédaction
                              email vit désormais dans le hook, appliquée
                              uniformément à chaque appel d'outil.

agent/services/discovery.py  — NON TOUCHÉ dans cette phase (Phase 2b complète)
```

## 3. Composants

### 3.1 Preset `triage`

`build_triage_options(tenant_id, system_prompt=None) -> ClaudeAgentOptions` : `model=MODEL_TRIAGE`, `system_prompt=TRIAGE_PROMPT` (texte déménagé tel quel depuis `triage.py`), `max_turns=1`, aucun `mcp_servers`, aucun hook. Le budget est déjà vérifié en amont par `run_agent()` (fix de la Phase 1) indépendamment du preset — un hook `PreToolUse` ne servirait à rien ici puisqu'aucun outil n'est jamais appelé par ce preset.

### 3.2 Preset `taxonomy`

`build_taxonomy_options(tenant_id, system_prompt=None) -> ClaudeAgentOptions` : `model=MODEL_DIAGNOSTIC`, nouveau `TAXONOMY_PROMPT` qui instruit explicitement l'agent d'interroger `query_loki` lui-même (`stream_type="business"`) avant de conclure — mitigation best-effort par prompt, pas une garantie codée. `mcp_servers={"vigie-obs": build_obs_mcp_server(tenant_id)}` (réutilisé tel quel depuis la Phase 1 — donne accès à `query_loki`/`query_prometheus`/`query_traces`, YAGNI de ne pas créer un serveur restreint pour ce seul usage). `max_turns=3`. Hooks : `PreToolUse=[make_budget_guard_hook, make_tenant_scope_hook]` (réutilisés tels quels), `PostToolUse=[make_audit_hook, anonymize_hook]`.

### 3.3 `anonymize_hook` (nouveau, générique)

Hook `PostToolUse` sans état (pas de factory `make_*_hook(tenant_id)` puisqu'il ne dépend d'aucun paramètre par tenant) : inspecte `tool_response`, rédige les emails (regex reprise de l'actuel `taxonomy.py::anonymize()`) et renvoie le contenu corrigé via le champ `updatedMCPToolOutput`/`updatedToolOutput` du hook (à confirmer par spike, cf. §6). Ajouté à `PostToolUse` de **tous** les presets qui exposent des outils (`diagnostic` et `taxonomy` à ce stade — `triage` n'a pas d'outils, `discovery` est hors périmètre).

### 3.4 `agent/harness/runner.py`

`_PRESET_BUILDERS` étendu avec `"triage": build_triage_options` et `"taxonomy": build_taxonomy_options`. Le court-circuit mock (`VIGIE_MOCK_LLM=1`) devient sensible au preset : renvoie un JSON valide pour `triage`, un YAML valide pour `taxonomy`, le texte diagnostic existant pour `diagnostic` — remplace le `MOCK_DIAGNOSTIC_ANSWER` unique actuel par un petit dict de réponses fixtures par preset.

### 3.5 `agent/services/triage.py`

`triage_alert(tenant_id, signature, context)` appelle `run_agent("triage", context, tenant_id=tenant_id, endpoint="triage")` au lieu de `create_message()` directement. Le parsing JSON (regex + fallback) reste identique — c'est indépendant de la façon dont on appelle le LLM. Le cache (`_cache_get`/`_cache_set`) est inchangé.

### 3.6 `agent/services/taxonomy.py`

`propose_taxonomy(tenant_id, days=7)` : construit un prompt du type *"Explore les logs métier (stream_type=business) des {days} derniers jours et propose une taxonomie YAML..."*, appelle `run_agent("taxonomy", prompt, tenant_id=tenant_id, endpoint="taxonomy")`, parse le YAML retourné (logique de parsing/fallback inchangée : `yaml.safe_load` avec repli sur `{"events": [], "raw": text}`), écrit `{tenant_id}.proposed.yaml` comme aujourd'hui. Les fonctions `sample_logs()` et `anonymize()` sont supprimées (leur rôle est repris par l'agent + le hook `anonymize`).

## 4. Gestion d'erreurs

Budget épuisé ou erreur harness (Phase 1) → `run_agent()` renvoie une chaîne d'erreur explicite. Pour la taxonomie, cette chaîne tombe naturellement dans le fallback YAML existant (`raw: <message>`, `events: []`) — aucun code spécial à ajouter. Pour le triage, le parsing JSON existant bascule déjà sur `is_anomaly = "false" not in text.lower()` en cas d'échec de parsing — comportement conservé tel quel (une chaîne d'erreur contenant "budget" ferait probablement basculer vers `is_anomaly=True` par ce chemin de repli existant, ce qui est le comportement prudent : en cas de doute, ne pas supprimer une alerte).

## 5. Tests

- `tests/unit/test_harness_options.py` : nouveaux cas pour `build_triage_options` (0 outil, `max_turns=1`) et `build_taxonomy_options` (`mcp_servers` contient `vigie-obs`, `max_turns=3`, les 4 hooks présents).
- `tests/unit/test_harness_hooks.py` : nouveaux cas pour `anonymize_hook` (email présent → rédigé ; pas d'email → `{}` inchangé).
- `tests/unit/test_harness_runner.py` : mock sensible au preset (`triage` → JSON valide, `taxonomy` → YAML valide).
- **Nouveau** `tests/unit/test_triage.py` et `tests/unit/test_taxonomy.py` : aucun test n'existe aujourd'hui pour `triage_alert()` ni `propose_taxonomy()` directement — comble ce trou puisque ces fonctions sont modifiées en profondeur par cette migration.

## 6. Point à vérifier par spike avant implémentation

Phase 1 n'a confirmé par un run réel que le comportement `PreToolUse` (nommage des outils, `permission_mode`). La forme exacte de `tool_response` reçue par un hook `PostToolUse`, et le format exact attendu par `updatedMCPToolOutput`/`updatedToolOutput` pour réellement modifier ce qui revient au modèle, restent à confirmer par un run réel avant d'écrire `anonymize_hook` — même précaution qui a évité de mal deviner `permission_mode` en Phase 1 (où la simple lecture de la documentation du SDK aurait conduit à un design cassé en production). Le plan d'implémentation inclut une tâche de spike dédiée pour ce point, sur le même modèle que la Task 2 de la Phase 1.

## 7. Hors périmètre de cette phase

- `discovery.py` : intégralement reporté à la Phase 2b (migration + enrichissement avec un nouveau serveur MCP `fs_scan` read-only wrappant `discovery/scanner.py`).
- Retrait de `agent/services/llm_client.py` : possible seulement une fois discovery migré aussi (Phase 2b) — `llm_client.py` reste utilisé par `discovery.py` jusque-là.
- Extension du hook d'anonymisation à des données autres que les emails (ex. numéros de téléphone, IBAN) : non demandé, YAGNI.
