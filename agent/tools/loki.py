"""Outils LogQL exposés à l'agent."""

import re
import time
from datetime import datetime

import httpx

from agent.config import LOKI_URL, MAX_LOG_LINES

_TENANT_LABEL_RE = re.compile(r'tenant_id\s*=\s*"([^"]+)"')


def _scope_logql_to_tenant(logql: str, tenant_id: str) -> str:
    """Force le scope tenant sur toute requête LogQL, quelle que soit sa forme.

    Remplace l'ancien garde-fou porté par un hook PreToolUse (incompatible avec
    les sous-agents du preset ask) : l'isolation multi-tenant est désormais
    appliquée directement dans l'outil, donc effective sur tous les chemins.
    """
    existing = _TENANT_LABEL_RE.search(logql)
    if existing:
        # Un tenant est déjà spécifié : on n'autorise que le tenant courant.
        if existing.group(1) != tenant_id:
            raise PermissionError(
                f"Requête référence un tenant non autorisé: {existing.group(1)}"
            )
        return logql
    # Aucun tenant explicite : on injecte le scope correct, y compris pour les
    # sélecteurs de flux déjà présents (ex: '{level="error"}').
    if logql.startswith("{"):
        return logql[:1] + f'tenant_id="{tenant_id}",' + logql[1:]
    return f'{{tenant_id="{tenant_id}"}} ' + logql


async def run_query_loki(
    logql: str,
    hours_back: float = 24,
    limit: int = 100,
    tenant_id: str | None = None,
) -> str:
    limit = min(limit, MAX_LOG_LINES)
    if tenant_id:
        try:
            logql = _scope_logql_to_tenant(logql, tenant_id)
        except PermissionError as e:
            return f"Refusé : {e}"
    end = time.time_ns()
    start = end - int(hours_back * 3600 * 1e9)
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": logql, "start": start, "end": end, "limit": limit},
        )
    if r.status_code != 200:
        return f"Erreur Loki ({r.status_code}): {r.text[:500]}"
    data = r.json().get("data", {}).get("result", [])
    lines = []
    for stream in data:
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            dt = datetime.fromtimestamp(int(ts) / 1e9).isoformat(timespec="seconds")
            lines.append(f"[{dt}] {labels.get('level', '?')} | {line[:300]}")
    if not lines:
        return "Aucun résultat pour cette requête sur la fenêtre demandée."
    return "\n".join(lines[:limit])
