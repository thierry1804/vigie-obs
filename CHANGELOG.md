# Changelog VIGIE

## [Unreleased]

## 3.0.1 — Stabilisation post-migration harness

### Fixed
- Le CLI `claude` refusait `permission_mode="bypassPermissions"` quand le conteneur agent tourne en root (`--dangerously-skip-permissions cannot be used with root/sudo privileges`), faisant échouer tout appel harness avec `Command failed with exit code 1`. Ajoute `IS_SANDBOX=1` (échappatoire officielle du CLI pour environnement conteneurisé) dans `agent/Dockerfile` et `docker-compose.yml`.
- Loki (`vigie-loki`) partait en crash-loop au démarrage : `retention_enabled: true` sans `compactor.delete_request_store` est rejeté par la validation de config depuis Loki 2.9+/3.x (« compactor.delete-request-store should be configured when retention is enabled »). Ajoute `delete_request_store: filesystem` dans `config/loki.yaml`.
- Durcit l'isolation multi-tenant des requêtes LogQL directement dans `agent/tools/loki.py::run_query_loki` (nouvelle fonction `_scope_logql_to_tenant`), plutôt que de compter uniquement sur le hook `PreToolUse` `make_tenant_scope_hook`. Nécessaire car deux chemins réels appellent `run_query_loki` sans jamais passer par les hooks du SDK : le serveur MCP externe (`agent/mcp/tools.py`) et le service d'alerting (`agent/services/alerting.py`).

### Changed
- `build_ask_options` (préset `ask`) repasse d'une conception « routeur pur + 2 sous-agents » à un agent racine unique doté des 5 outils (obs + biz). Un run réel a montré qu'un appel d'outil MCP fait depuis un sous-agent et gardé par un hook `PreToolUse` échoue de façon intermittente (« Stream closed », 4 échecs / 6 runs) alors que le même hook sur un appel fait par l'agent racine n'a jamais échoué (0/4) — voir [`docs/superpowers/harness-migration-status.md`](docs/superpowers/harness-migration-status.md) §4.14 pour le détail de l'investigation. Comportement externe inchangé (mêmes endpoints, mêmes contrats).

## 3.0.0 — Harness agentique (V3)

### Changed
- L'agent diagnostic (`/ask`, alerting, `mcp/explain_anomaly`) délègue désormais au Claude Agent SDK via `agent/harness/` au lieu d'un client Anthropic maison. Comportement externe inchangé (mêmes endpoints, même mode mock `VIGIE_MOCK_LLM=1`). Corrige au passage un blocage de la boucle événements asyncio pendant les appels LLM.
- L'image Docker de l'agent embarque désormais Node.js et le CLI `@anthropic-ai/claude-code`, requis par le nouveau harness.
- Migre `triage.py` vers le harness Claude Agent SDK (`run_agent("triage", ...)`) — `triage_alert()` devient asynchrone.
- Enrichit `taxonomy.py` : `propose_taxonomy()` devient un agent qui interroge lui-même `query_loki` au lieu d'un échantillonnage Python fixe.
- Ajoute `anonymize_hook` (rédaction email) sur tous les presets exposant des outils (diagnostic et taxonomie) — ferme un trou de confidentialité où l'agent diagnostic ne rédigeait jamais les emails présents dans les logs qu'il lit.
- Migre `discovery.py` vers le harness Claude Agent SDK — `infer_formats()`/`run_discovery()` deviennent asynchrones. Corrige un bug où la réponse LLM était calculée mais jamais utilisée (seule une heuristique Python décidait du `framework_hint`) : la classification est désormais réellement pilotée par l'agent, via deux outils bornés (`sample_lines`, `set_framework_hint`) qui n'acceptent jamais de chemin arbitraire.
- Ajoute l'agent orchestrateur `ask` (harness) : `/ask` et `mcp/explain_anomaly` délèguent désormais à deux sous-agents spécialisés (`diagnostic-investigator` pour Loki/Prometheus/Tempo, `business-analyst` pour KPIs/taxonomie métier via le nouveau serveur MCP `vigie-biz`) au lieu d'un unique preset diagnostic figé. Comble le trou fonctionnel où l'agent diagnostic n'avait jamais accès aux données métier. `report/daily` et l'alerting restent inchangés (preset diagnostic direct).
- Remplace le serveur MCP externe (`agent/mcp/server.py`) par un vrai serveur conforme au protocole MCP (SDK `mcp` officiel, `FastMCP`, transport Streamable HTTP JSON-RPC) — auparavant du REST FastAPI qui imitait seulement des noms d'outils MCP. Remplacement complet, pas de double-stack (aucun client externe réel n'existait encore, seulement un client de test simulant proto-factory). Auth par `TokenVerifier` custom réutilisant `Tenant.mcp_token` à l'identique ; le header `X-Tenant-ID` disparaît (le tenant vient uniquement du token, comme c'était déjà le cas en pratique).

## 2.0.0 — Plateforme (V2)

- Serveur MCP (4 outils) + auth jetons par tenant
- Stack Tempo + otel-collector
- SDK OTel optionnel Symfony + Node
- Outil agent `query_traces`
- Tests isolation multi-tenant (10 scénarios CI)
- Audit trail structuré par tenant

## 1.0.0 — Prêt à vendre (V1)

- Discovery automatique (`vigie discover`, `POST /discover`)
- Taxonomie métier apprise (`vigie taxonomy`)
- Alerting proactif Haiku → Sonnet + Slack/email
- Multi-tenant partiel (`tenant_id`, budget LLM)
- Persistance SQLite (anomalies, règles, tokens)
- CLI Typer, CI pytest+ruff

## 0.1.0 — Prototype (V0)

- Collecte Vector/Loki/Prometheus/Grafana
- Agent `/ask` + `/report/daily`
