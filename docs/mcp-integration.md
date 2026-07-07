# Intégration MCP VIGIE

VIGIE expose 4 outils MCP via REST (compatible agents ETECH).

## Authentification

Header : `Authorization: Bearer <mcp_token>` (configuré par tenant en base).

Optionnel : `X-Tenant-ID` doit correspondre au tenant du token.

## Outils

| Outil | Endpoint | Body |
|---|---|---|
| get_project_health | POST /mcp/tools/get_project_health | `{"hours": 24}` |
| query_incidents | POST /mcp/tools/query_incidents | `{"hours": 168, "status": "open"}` |
| get_business_kpis | POST /mcp/tools/get_business_kpis | `{"hours": 24}` |
| explain_anomaly | POST /mcp/tools/explain_anomaly | `{"anomaly_id": 1}` ou `{"question": "..."}` |

## SSE

`GET /mcp/sse` — annonce la liste des outils (transport SSE).

## Exemple curl

```bash
curl -X POST http://localhost:8080/mcp/tools/get_project_health \
  -H "Authorization: Bearer default-mcp-token" \
  -H "Content-Type: application/json" \
  -d '{"hours": 24}'
```

## proto-factory

Intégration réelle pending — utiliser le client test `tests/integration/test_mcp_client.py` en labo.
