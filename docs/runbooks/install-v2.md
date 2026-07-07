# Installation VIGIE V2 (plateforme)

Inclut V1 + Tempo/OTel optionnel + serveur MCP.

## Stack complète

```bash
docker compose up -d --build
```

Services additionnels V2 :
- `tempo` — traces distribuées
- `otel-collector` — réception OTLP (4317/4318)

## SDK OTel (upsell optionnel)

### Symfony

```php
use Etech\VigieOtel\VigieOtel;
VigieOtel::init('mon-app', tenantId: 'client_x');
```

### Node

```js
const { initVigieOtel } = require('@etech/vigie-otel');
initVigieOtel({ serviceName: 'mon-api', tenantId: 'client_x' });
```

## MCP (agents ETECH)

Endpoints sous `/mcp/tools/` avec `Authorization: Bearer <mcp_token>`.

Outils : `get_project_health`, `query_incidents`, `get_business_kpis`, `explain_anomaly`.

Voir [mcp-integration.md](../mcp-integration.md).

## Désinstallation

```bash
./lab/teardown.sh
docker compose down -v
```

Réversibilité zéro-code : retirer les montages volumes et le stack Docker. SDK OTel : retirer le package Composer/npm.
