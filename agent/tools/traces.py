"""Outil traces Tempo (V2)."""

import json

import httpx

from agent.config import TEMPO_URL


async def run_query_traces(
    trace_id: str | None = None,
    service: str | None = None,
    hours_back: float = 24,
    limit: int = 20,
    tenant_id: str | None = None,
) -> str:
    async with httpx.AsyncClient(timeout=30) as http:
        if trace_id:
            r = await http.get(f"{TEMPO_URL}/api/traces/{trace_id}")
            if r.status_code != 200:
                return f"Erreur Tempo ({r.status_code}): {r.text[:500]}"
            return json.dumps(r.json(), ensure_ascii=False)[:8000]

        params = {"limit": limit}
        if service:
            params["service.name"] = service
        if tenant_id:
            params["tags"] = f'tenant.id="{tenant_id}"'
        r = await http.get(f"{TEMPO_URL}/api/search", params=params)
    if r.status_code != 200:
        return f"Erreur Tempo search ({r.status_code}): {r.text[:500]}"
    traces = r.json().get("traces", [])
    if not traces:
        return "Aucune trace trouvée."
    lines = []
    for t in traces[:limit]:
        lines.append(
            f"trace_id={t.get('traceID', '?')} service={t.get('rootServiceName', '?')} "
            f"duration={t.get('durationMs', '?')}ms"
        )
    return "\n".join(lines)
