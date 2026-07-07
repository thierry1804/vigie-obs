"""
VIGIE — Agent d'observabilité conversationnel (V0)
===================================================
Un agent LLM branché sur Loki (logs) et Prometheus (métriques) via tool use.

Endpoints :
  POST /ask          -> diagnostic conversationnel ("pourquoi le batch a pris 40 min ?")
  GET  /report/daily -> résumé quotidien (santé technique + événements métier inférés)
  GET  /health       -> healthcheck

Routage de modèles (même logique que proto-factory) :
  - MODEL_TRIAGE (Haiku)    : classification, filtrage — jamais appelé en V0,
                              réservé au streaming d'anomalies (V1)
  - MODEL_DIAGNOSTIC (Sonnet): analyse, corrélation, rédaction de rapports
"""

import os
import json
import time
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from anthropic import Anthropic

# --- Configuration -----------------------------------------------------

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
PROM_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
MODEL_DIAGNOSTIC = os.environ.get("MODEL_DIAGNOSTIC", "claude-sonnet-4-6")

client = Anthropic()  # ANTHROPIC_API_KEY lue depuis l'environnement
app = FastAPI(title="VIGIE Agent", version="0.1.0")

MAX_TOOL_TURNS = 8          # garde-fou budget : nb max d'allers-retours outils
MAX_LOG_LINES = 200         # tronquer les résultats Loki avant envoi au LLM

# --- Outils exposés à l'agent ------------------------------------------

TOOLS = [
    {
        "name": "query_loki",
        "description": (
            "Interroge les logs centralisés via une requête LogQL. "
            "Labels disponibles : projet, level (error/warning/info), "
            "stream_type (business/technical). "
            "Exemple : {level=\"error\"} |= \"timeout\""
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "logql": {"type": "string", "description": "Requête LogQL"},
                "hours_back": {"type": "number", "description": "Fenêtre temporelle en heures (défaut 24)"},
                "limit": {"type": "integer", "description": "Nb max de lignes (défaut 100)"},
            },
            "required": ["logql"],
        },
    },
    {
        "name": "query_prometheus",
        "description": (
            "Exécute une requête PromQL instantanée ou de plage. "
            "Métriques node_exporter disponibles : node_cpu_seconds_total, "
            "node_memory_MemAvailable_bytes, node_filesystem_avail_bytes, "
            "node_load1, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "promql": {"type": "string", "description": "Requête PromQL"},
                "range_hours": {
                    "type": "number",
                    "description": "Si fourni, requête de plage sur N heures (step auto)",
                },
            },
            "required": ["promql"],
        },
    },
]


async def run_query_loki(logql: str, hours_back: float = 24, limit: int = 100) -> str:
    limit = min(limit, MAX_LOG_LINES)
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
            lines.append(f"[{dt}] {labels.get('level','?')} | {line[:300]}")
    if not lines:
        return "Aucun résultat pour cette requête sur la fenêtre demandée."
    return "\n".join(lines[:limit])


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
    return json.dumps(r.json().get("data", {}), ensure_ascii=False)[:8000]


async def dispatch_tool(name: str, args: dict) -> str:
    if name == "query_loki":
        return await run_query_loki(**args)
    if name == "query_prometheus":
        return await run_query_prometheus(**args)
    return f"Outil inconnu : {name}"


# --- Boucle agentique ---------------------------------------------------

SYSTEM_PROMPT = """Tu es VIGIE, un agent d'observabilité branché sur un projet en production.
Tu as accès aux logs (Loki/LogQL) et aux métriques système (Prometheus/PromQL).

Méthode de diagnostic (boucle Plan-Exécute-Vérifie) :
1. PLAN : formule une hypothèse et la ou les requêtes qui la testeraient.
2. EXÉCUTE : lance les requêtes nécessaires (commence large, affine ensuite).
3. VÉRIFIE : avant de conclure, challenge ta propre hypothèse — existe-t-il
   une explication alternative que les données n'excluent pas ? Si oui, teste-la.

Règles :
- Distingue toujours les FAITS (observés dans les données) des HYPOTHÈSES.
- Si les données sont insuffisantes pour conclure, dis-le et propose quelle
  instrumentation supplémentaire lèverait l'ambiguïté.
- Réponds en français, de façon concise et actionnable.
- Pour les événements métier, exploite le label stream_type="business"."""


async def agent_loop(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]
    for _ in range(MAX_TOOL_TURNS):
        response = client.messages.create(
            model=MODEL_DIAGNOSTIC,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await dispatch_tool(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        messages.append({"role": "user", "content": tool_results})

    return "Budget d'investigation épuisé sans conclusion ferme — affinez la question."


# --- API ------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str


@app.post("/ask")
async def ask(req: AskRequest):
    answer = await agent_loop(req.question)
    return {"answer": answer}


@app.get("/report/daily")
async def daily_report():
    prompt = (
        "Génère le rapport quotidien du projet observé pour les dernières 24h. "
        "Structure attendue : 1) Santé technique (erreurs, tendances, saturation "
        "ressources), 2) Activité métier (volumes et types d'événements sur "
        "stream_type=business), 3) Points d'attention et recommandations. "
        "Sois factuel et bref."
    )
    report = await agent_loop(prompt)
    return {"date": datetime.now().date().isoformat(), "report": report}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "vigie-agent"}
