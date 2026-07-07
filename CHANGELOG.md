# Changelog VIGIE

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
