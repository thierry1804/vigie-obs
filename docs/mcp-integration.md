# Intégration MCP VIGIE

VIGIE expose 4 outils via un vrai serveur MCP (protocole JSON-RPC, transport Streamable HTTP, compatible agents ETECH).

## Authentification

Header : `Authorization: Bearer <mcp_token>` (configuré par tenant en base) — envoyé à la négociation de session MCP, comme n'importe quel client MCP standard.

## Outils

| Outil | Paramètres |
|---|---|
| get_project_health | `hours: float = 24` |
| query_incidents | `hours: float = 168`, `status: str \| None = None` |
| get_business_kpis | `hours: float = 24` |
| explain_anomaly | `anomaly_id: int \| None = None`, `question: str \| None = None` |

## Exemple client (Python, SDK `mcp` officiel)

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    "http://localhost:8080/mcp", headers={"Authorization": "Bearer <mcp_token>"}
) as (read, write, _get_session_id):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("get_project_health", {"hours": 24})
```

## proto-factory

Le serveur est désormais conforme au protocole MCP standard — ce qui était bloquant côté VIGIE ne l'est plus. L'intégration réelle avec un client proto-factory reste *pending* côté écosystème ETECH (hors du contrôle de ce dépôt). En attendant, `tests/integration/test_mcp_protocol.py` sert de client de référence en labo.
