"""Outils LogQL exposés à l'agent."""

import time
from datetime import datetime

import httpx

from agent.config import LOKI_URL, MAX_LOG_LINES


async def run_query_loki(
    logql: str,
    hours_back: float = 24,
    limit: int = 100,
    tenant_id: str | None = None,
) -> str:
    limit = min(limit, MAX_LOG_LINES)
    if tenant_id and "tenant_id" not in logql:
        logql = f'{{tenant_id="{tenant_id}"}} ' + logql if not logql.startswith("{") else logql
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
