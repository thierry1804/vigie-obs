"""Outils PromQL exposés à l'agent."""

import json
import time

import httpx

from agent.config import MAX_PROM_RESULT_CHARS, PROM_URL


async def run_query_prometheus(promql: str, range_hours: float | None = None) -> str:
    async with httpx.AsyncClient(timeout=30) as http:
        if range_hours:
            end = time.time()
            start = end - range_hours * 3600
            step = max(int(range_hours * 3600 / 100), 15)
            r = await http.get(
                f"{PROM_URL}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
            )
        else:
            r = await http.get(f"{PROM_URL}/api/v1/query", params={"query": promql})
    if r.status_code != 200:
        return f"Erreur Prometheus ({r.status_code}): {r.text[:500]}"
    return json.dumps(r.json().get("data", {}), ensure_ascii=False)[:MAX_PROM_RESULT_CHARS]
