# Changelog VIGIE

## [Unreleased]

### Changed
- L'agent diagnostic (`/ask`, alerting, `mcp/explain_anomaly`) délègue désormais au Claude Agent SDK via `agent/harness/` au lieu d'un client Anthropic maison. Comportement externe inchangé (mêmes endpoints, même mode mock `VIGIE_MOCK_LLM=1`). Corrige au passage un blocage de la boucle événements asyncio pendant les appels LLM.
- L'image Docker de l'agent embarque désormais Node.js et le CLI `@anthropic-ai/claude-code`, requis par le nouveau harness.
- Migre `triage.py` vers le harness Claude Agent SDK (`run_agent("triage", ...)`) — `triage_alert()` devient asynchrone.
- Enrichit `taxonomy.py` : `propose_taxonomy()` devient un agent qui interroge lui-même `query_loki` au lieu d'un échantillonnage Python fixe.
- Ajoute `anonymize_hook` (rédaction email) sur tous les presets exposant des outils (diagnostic et taxonomie) — ferme un trou de confidentialité où l'agent diagnostic ne rédigeait jamais les emails présents dans les logs qu'il lit.

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
