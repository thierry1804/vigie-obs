# Harness Phase 4 — vrai serveur MCP externe : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer `agent/mcp/server.py` (REST FastAPI qui imite des noms d'outils MCP) par un vrai serveur MCP protocolaire — SDK Python officiel `mcp`, API `FastMCP`, transport Streamable HTTP, auth par `TokenVerifier` custom réutilisant `Tenant.mcp_token`. Remplacement complet, pas de double-stack.

**Architecture:** `agent/mcp/auth.py` (vérification de token), `agent/mcp/tools.py` (logique des 4 outils, indépendante du transport), `agent/mcp/server.py::build_mcp_server()` (factory `FastMCP`, jamais de singleton — voir erratum ci-dessous), instanciée fraîchement à chaque cycle de `lifespan` dans `agent/main.py` et exposée aux requêtes via un petit wrapper ASGI monté une fois sur `app.mount("/mcp", _mcp_asgi)`.

**Tech Stack:** Python 3.12+, paquet `mcp` 1.28.1 (déjà installé en transitif via `claude-agent-sdk`, ajouté en dépendance directe), FastAPI/Starlette, `uvicorn` (déjà une dépendance, réutilisé pour les tests bout-en-bout), pytest (`asyncio_mode = "auto"`), `pytest-httpx`.

## Global Constraints

Faits SDK confirmés par lecture directe du paquet `mcp` 1.28.1 installé (`.venv/lib/python3.13/site-packages/mcp/`) pendant le brainstorming — **ne pas re-vérifier, mais toute divergence observée pendant l'implémentation doit être signalée** (cohérent avec la pratique du projet : jamais deviner un fait SDK, `docs/superpowers/harness-migration-status.md` §4) :

- `FastMCP.__init__` accepte `token_verifier: TokenVerifier | None` et `auth: AuthSettings | None` comme kwargs directs (pas imbriqués l'un dans l'autre) — `mcp/server/fastmcp/server.py:147-176`.
- `AuthSettings` (`mcp/server/auth/settings.py:15`) a deux champs **obligatoires** (`Field(...)`, sans défaut) : `issuer_url: AnyHttpUrl` et `resource_server_url: AnyHttpUrl | None`. Le second accepte explicitement `None` comme valeur — passer `resource_server_url=None` désactive les routes `.well-known/oauth-protected-resource` (`create_protected_resource_routes`, non voulues ici, cf. §10 du design : pas de vraie autorité OAuth). `issuer_url` doit rester une URL valide même si elle n'est lue par aucun code tant que `auth_server_provider` n'est pas configuré (ce qui est le cas ici — on ne configure jamais `auth_server_provider`).
- Sans `auth=AuthSettings(...)` fourni, `token_verifier` est **inopérant** : `streamable_http_app()` (`mcp/server/fastmcp/server.py:974-985`) ne construit `AuthenticationMiddleware`/`AuthContextMiddleware` que si `self.settings.auth` est vérité — donc les deux doivent être fournis ensemble.
- `@server.tool()` / `server.add_tool(fn)` (`mcp/server/fastmcp/server.py:397-505`) **retourne la fonction Python inchangée** (pas de wrapping dans un objet type `SdkMcpTool` comme dans `claude_agent_sdk`) — un outil enregistré reste directement appelable/testable comme une fonction Python normale.
- Le tenant authentifié est récupéré dans un tool handler via `mcp.server.auth.middleware.auth_context.get_access_token() -> AccessToken | None`, qui lit une `contextvars.ContextVar` posée par `AuthContextMiddleware` **avant** l'appel du handler — pas de propriété `.auth`/`.claims` sur l'objet `Context` de FastMCP.
- `BearerAuthBackend.authenticate()` (`mcp/server/auth/middleware/bearer_auth.py`) attend exactement `Authorization: Bearer <token>` — identique au format actuel, aucun changement côté client sur ce point précis.
- `streamable_http_app()` retourne un `Starlette` dont le `lifespan` interne (`lambda app: self.session_manager.run()`) **ne se déclenche que si cette app tourne comme app racine** — un `Mount` Starlette ne propage pas automatiquement le lifespan d'une sous-app. `mcp_server.session_manager.run()` (context manager async, propriété publique `FastMCP.session_manager`, `mcp/server/fastmcp/server.py:261`) doit donc être entré explicitement dans le `lifespan` d'`agent/main.py`.
- Le SDK n'offre **aucun raccourci officiel** pour tester le transport Streamable HTTP + le pipeline d'auth sans un vrai port TCP (`mcp.shared.memory.create_connected_server_and_client_session` court-circuite tout ça). Les tests de la Task 4 lancent donc un vrai `uvicorn.Server` — comme tâche asyncio dans la boucle du test (pas besoin d'un thread OS séparé, `uvicorn.Server.serve()` est déjà une coroutine).
- Le mécanisme d'auth exact (URL de montage effective après `streamable_http_path="/"` + `app.mount("/mcp", ...)`, forme exacte de l'erreur sur token invalide) est **empirique par construction** — la Task 4 est un point de vérification réel, pas une supposition. Si le premier essai de connexion échoue avec une 404 ou une erreur de forme inattendue, ajuster `streamable_http_path`/l'URL utilisée par le test et documenter ce qui a été trouvé dans le rapport, plutôt que de deviner à l'avance.
- **Erratum découvert pendant l'implémentation de la Task 3, confirmé par lecture directe de `mcp/server/streamable_http_manager.py`** : `StreamableHTTPSessionManager.run()` ne peut être entré **qu'une seule fois par instance, jamais réutilisable après sortie** (« Important: Only one StreamableHTTPSessionManager instance should be created per application. The instance cannot be reused after its run() context has completed. »). Un singleton `mcp_server`/`mcp_app` construit une fois au niveau module (comme l'esquissait la version initiale de ce plan) casse dès qu'une deuxième requête ASGI `lifespan` a lieu dans le même process — exactement ce que fait ce dépôt de tests, qui recrée un `TestClient(app)` par fonction de test dans la plupart des fichiers, chacun redéclenchant tout le `lifespan` d'`agent/main.py`. La Task 3 ci-dessous est corrigée en conséquence : `build_mcp_server()` (déjà une factory) est appelée **à l'intérieur du `lifespan`**, une instance fraîche par cycle — sans impact en production (un seul cycle de toute façon, un process = un démarrage). Le montage Starlette ne peut pas référencer statiquement l'app ainsi reconstruite (un `Mount` est fixé au moment de la construction d'`app`, avant que `lifespan` ne tourne) ; un petit wrapper ASGI monté à la place délègue à `scope["app"].state.mcp_app`, peuplé fraîchement par chaque cycle de `lifespan` avant le `yield` — mécanisme standard Starlette (`scope["app"]` est injecté par l'app racine dans tout appel imbriqué, y compris les sous-apps montées).

## Task 1 : Vérification de token (`agent/mcp/auth.py`)

**Files:**
- Create: `agent/mcp/auth.py`
- Test: `tests/unit/test_mcp_auth.py`

**Interfaces:**
- Consumes : `agent.db.models.Tenant` (champ `mcp_token`), `agent.db.session.get_session()` (context manager synchrone, déjà utilisé partout ailleurs dans `agent/mcp/server.py` actuel).
- Produces : `VigieTokenVerifier` (classe, méthode async `verify_token(token: str) -> AccessToken | None`) — consommé par la Task 3.

- [ ] **Step 1: Écrire les tests (échouent, le module n'existe pas encore)**

Créer `tests/unit/test_mcp_auth.py` :

```python
import pytest

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.mcp.auth import VigieTokenVerifier


@pytest.mark.asyncio
async def test_verify_token_returns_access_token_for_known_tenant():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", mcp_token="tok-acme"))
        session.commit()

    verifier = VigieTokenVerifier()
    access_token = await verifier.verify_token("tok-acme")

    assert access_token is not None
    assert access_token.client_id == "acme"
    assert access_token.subject == "acme"
    assert access_token.claims == {"tenant_id": "acme"}
    assert access_token.scopes == ["mcp"]


@pytest.mark.asyncio
async def test_verify_token_returns_none_for_unknown_token():
    verifier = VigieTokenVerifier()
    access_token = await verifier.verify_token("does-not-exist")
    assert access_token is None
```

- [ ] **Step 2: Lancer les tests, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.mcp.auth'`

- [ ] **Step 3: Implémenter `agent/mcp/auth.py`**

```python
"""Vérification des tokens MCP externes — TokenVerifier custom pour le SDK mcp."""

from mcp.server.auth.provider import AccessToken, TokenVerifier

from agent.db.models import Tenant
from agent.db.session import get_session


class VigieTokenVerifier(TokenVerifier):
    """Vérifie un token MCP contre Tenant.mcp_token — même logique qu'avant l'API SDK."""

    async def verify_token(self, token: str) -> AccessToken | None:
        with get_session() as session:
            tenant = session.query(Tenant).filter(Tenant.mcp_token == token).first()
        if not tenant:
            return None
        return AccessToken(
            token=token,
            client_id=tenant.id,
            scopes=["mcp"],
            subject=tenant.id,
            claims={"tenant_id": tenant.id},
        )
```

- [ ] **Step 4: Lancer les tests, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_auth.py -v`
Expected: PASS — 2 tests verts.

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/mcp/auth.py tests/unit/test_mcp_auth.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/mcp/auth.py tests/unit/test_mcp_auth.py
git commit -m "feat: VigieTokenVerifier — vérification de token MCP externe (SDK mcp)"
```

## Task 2 : Logique des 4 outils (`agent/mcp/tools.py`)

**Files:**
- Create: `agent/mcp/tools.py`
- Test: `tests/unit/test_mcp_tools.py`

**Interfaces:**
- Consumes : `agent.db.models.Anomaly`, `agent.db.session.get_session()`, `agent.services.taxonomy.load_taxonomy(tenant_id)`, `agent.tools.loki.run_query_loki(...)`, `agent.tools.prometheus.run_query_prometheus(...)`, `agent.harness.runner.run_agent(preset, user_message, tenant_id=..., endpoint=...)` — tous déjà existants et inchangés.
- Produces : 4 fonctions async (`get_project_health`, `query_incidents`, `get_business_kpis`, `explain_anomaly`) directement appelables ; `register_tools(server: FastMCP) -> None` — consommé par la Task 3.

- [ ] **Step 1: Écrire les tests (échouent, le module n'existe pas encore)**

Créer `tests/unit/test_mcp_tools.py` :

```python
import re

import pytest

import agent.mcp.tools as mcp_tools
from agent.db.models import Anomaly
from agent.db.session import get_session


@pytest.fixture(autouse=True)
def _fixed_tenant(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_current_tenant_id", lambda: "acme")


@pytest.mark.asyncio
async def test_get_project_health_returns_status(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(r"http://loki:3100/loki/api/v1/query_range.*"),
        json={"data": {"result": []}},
    )
    httpx_mock.add_response(
        url=re.compile(r"http://prometheus:9090/api/v1/query.*"),
        json={"data": {"result": []}},
    )
    result = await mcp_tools.get_project_health(hours=24)
    assert result["tenant_id"] == "acme"
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_query_incidents_scoped_to_tenant():
    with get_session() as session:
        session.add(Anomaly(tenant_id="acme", signature="s1", title="Acme only", status="open"))
        session.add(Anomaly(tenant_id="other", signature="s2", title="Other only", status="open"))
        session.commit()

    result = await mcp_tools.query_incidents(hours=168)

    titles = [i["title"] for i in result["incidents"]]
    assert "Acme only" in titles
    assert "Other only" not in titles


@pytest.mark.asyncio
async def test_get_business_kpis_empty_without_taxonomy():
    result = await mcp_tools.get_business_kpis(hours=24)
    assert result["tenant_id"] == "acme"
    assert result["kpis"] == {}


@pytest.mark.asyncio
async def test_explain_anomaly_calls_run_agent_with_ask_preset(monkeypatch):
    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["endpoint"] = endpoint
        return "diagnostic factice"

    monkeypatch.setattr(mcp_tools, "run_agent", fake_run_agent)

    result = await mcp_tools.explain_anomaly(question="Pic CPU hier ?")

    assert result["diagnosis"] == "diagnostic factice"
    assert captured == {"preset": "ask", "endpoint": "mcp/explain_anomaly"}


@pytest.mark.asyncio
async def test_register_tools_adds_all_four():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    mcp_tools.register_tools(server)

    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
```

- [ ] **Step 2: Lancer les tests, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.mcp.tools'`

- [ ] **Step 3: Implémenter `agent/mcp/tools.py`**

```python
"""Logique des 4 outils MCP externes — indépendante du transport/serveur."""

from datetime import datetime, timedelta
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP

from agent.db.models import Anomaly
from agent.db.session import get_session
from agent.harness.runner import run_agent
from agent.services.taxonomy import load_taxonomy
from agent.tools.loki import run_query_loki
from agent.tools.prometheus import run_query_prometheus


def _current_tenant_id() -> str:
    access_token = get_access_token()
    return access_token.claims["tenant_id"]


async def get_project_health(hours: float = 24) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    errors = await run_query_loki(
        '{level="error"}', hours_back=hours, limit=20, tenant_id=tenant_id
    )
    cpu = await run_query_prometheus(
        '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    )
    taxonomy = load_taxonomy(tenant_id)
    business_types = [e["name"] for e in (taxonomy or {}).get("events", [])]
    return {
        "tenant_id": tenant_id,
        "window_hours": hours,
        "technical": {"errors_sample": errors[:500], "cpu_query": cpu[:300]},
        "business": {"event_types": business_types},
        "status": "degraded" if "error" in errors.lower()[:100] else "ok",
    }


async def query_incidents(hours: float = 168, status: str | None = None) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    since = datetime.utcnow() - timedelta(hours=hours)
    with get_session() as session:
        q = session.query(Anomaly).filter(
            Anomaly.tenant_id == tenant_id, Anomaly.created_at >= since
        )
        if status:
            q = q.filter(Anomaly.status == status)
        rows = q.order_by(Anomaly.created_at.desc()).limit(50).all()
    return {
        "incidents": [
            {
                "id": r.id,
                "status": r.status,
                "title": r.title,
                "rule_name": r.rule_name,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


async def get_business_kpis(hours: float = 24) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    taxonomy = load_taxonomy(tenant_id)
    kpis: dict[str, Any] = {}
    if taxonomy:
        for ev in taxonomy.get("events", []):
            name = ev["name"]
            result = await run_query_loki(
                f'{{business_event_type="{name}"}}',
                hours_back=hours,
                limit=5,
                tenant_id=tenant_id,
            )
            count = max(
                0, result.count("\n") + (1 if result and "Aucun" not in result else 0)
            )
            kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
    return {"tenant_id": tenant_id, "window_hours": hours, "kpis": kpis}


async def explain_anomaly(
    anomaly_id: int | None = None, question: str | None = None
) -> dict[str, Any]:
    tenant_id = _current_tenant_id()
    context = question or ""
    if anomaly_id:
        with get_session() as session:
            a = session.get(Anomaly, anomaly_id)
            if a and a.tenant_id == tenant_id:
                context = f"Anomalie {a.title}: {a.diagnosis}"
    if not context:
        context = "Explique l'état de santé actuel du projet."
    diagnosis = await run_agent(
        "ask",
        f"Investigation structurée (FAITS/HYPOTHÈSES):\n{context}",
        tenant_id=tenant_id,
        endpoint="mcp/explain_anomaly",
    )
    return {"tenant_id": tenant_id, "diagnosis": diagnosis}


def register_tools(server: FastMCP) -> None:
    server.add_tool(get_project_health)
    server.add_tool(query_incidents)
    server.add_tool(get_business_kpis)
    server.add_tool(explain_anomaly)
```

- [ ] **Step 4: Lancer les tests, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_tools.py -v`
Expected: PASS — 6 tests verts.

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check agent/mcp/tools.py tests/unit/test_mcp_tools.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add agent/mcp/tools.py tests/unit/test_mcp_tools.py
git commit -m "feat: logique des 4 outils MCP externes, indépendante du transport"
```

## Task 3 : Assemblage du serveur + câblage dans l'app (`agent/mcp/server.py`, `agent/main.py`)

**Files:**
- Modify: `agent/mcp/server.py` (remplacement complet du contenu)
- Modify: `agent/main.py`
- Modify: `agent/requirements.txt`
- Test: `tests/unit/test_mcp_server_assembly.py`

**Interfaces:**
- Consumes : `VigieTokenVerifier` (Task 1), `register_tools` (Task 2).
- Produces : `agent.mcp.server.build_mcp_server() -> FastMCP` (factory, aucune instance module-level — voir erratum dans Global Constraints) — consommée uniquement par `agent/main.py`, appelée fraîchement à chaque cycle de `lifespan`. La Task 4 ne consomme rien directement de `agent.mcp.server` : elle pilote `agent.main.app` via de vraies requêtes HTTP.

**Correction (erratum ci-dessus)** : pas de singleton module-level `mcp_server`/`mcp_app`. `build_mcp_server()` reste une factory pure (déjà le cas dans la version précédente de ce plan) ; c'est `agent/main.py::lifespan()` qui en construit une instance fraîche à chaque entrée, et un petit wrapper ASGI monté une seule fois (à la construction d'`app`, comme avant) délègue à l'instance courante via `app.state`.

- [ ] **Step 1: Écrire le test (échoue, le module actuel n'a pas `build_mcp_server`)**

Créer `tests/unit/test_mcp_server_assembly.py` :

```python
import pytest
from starlette.applications import Starlette

from agent.mcp.server import build_mcp_server


def test_build_mcp_server_returns_starlette_app():
    server = build_mcp_server()
    assert isinstance(server.streamable_http_app(), Starlette)


@pytest.mark.asyncio
async def test_build_mcp_server_registers_all_four_tools():
    server = build_mcp_server()
    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
```

- [ ] **Step 2: Lancer le test, vérifier l'échec**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_server_assembly.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_mcp_server' from 'agent.mcp.server'`

- [ ] **Step 3: Remplacer le contenu de `agent/mcp/server.py`**

```python
"""Serveur MCP externe conforme au protocole (SDK mcp, transport Streamable HTTP)."""

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from agent.mcp.auth import VigieTokenVerifier
from agent.mcp.tools import register_tools

# Non utilisée par aucun code tant qu'aucun auth_server_provider n'est configuré (VIGIE
# ne fait pas office d'autorité OAuth) — requise uniquement par la validation Pydantic
# d'AuthSettings.issuer_url (champ obligatoire, sans défaut).
_ISSUER_URL = "http://vigie.local/"


def build_mcp_server() -> FastMCP:
    """Construit une instance FastMCP fraîche — jamais de singleton module-level.

    StreamableHTTPSessionManager (créé en interne par streamable_http_app()) ne peut
    être démarré qu'une seule fois par instance ; un singleton casserait dès le
    deuxième cycle de lifespan (chaque `with TestClient(app)` en recrée un). Voir
    agent/main.py::lifespan().
    """
    server = FastMCP(
        name="vigie",
        token_verifier=VigieTokenVerifier(),
        auth=AuthSettings(issuer_url=_ISSUER_URL, resource_server_url=None),
        streamable_http_path="/",
    )
    register_tools(server)
    return server
```

- [ ] **Step 4: Lancer le test, vérifier le succès**

Run: `PYTHONPATH=. .venv/bin/pytest tests/unit/test_mcp_server_assembly.py -v`
Expected: PASS — 2 tests verts. Si `FastMCP(...)` ou `AuthSettings(...)` lève une erreur de validation Pydantic sur `issuer_url`/`resource_server_url`, ajuster les valeurs et noter la divergence dans le rapport (fait SDK à corriger dans ce plan, pas à deviner).

- [ ] **Step 5: Câbler dans `agent/main.py`**

Remplacer l'import (ligne 11 actuelle) :
```python
from agent.mcp.server import build_mcp_server
```

Ajouter, juste avant la définition de `lifespan` (avant la ligne 34 actuelle), le petit wrapper ASGI qui délègue à l'app MCP courante :
```python
async def _mcp_asgi(scope, receive, send):
    await scope["app"].state.mcp_app(scope, receive, send)
```

Remplacer le `lifespan` (lignes 34-47 actuelles) :
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    scheduler.add_job(
        _alert_job,
        "interval",
        minutes=ALERT_INTERVAL_MINUTES,
        id="alert_cycle",
    )
    scheduler.start()
    logger.info("VIGIE agent v%s démarré", APP_VERSION)
    mcp_server = build_mcp_server()
    app.state.mcp_app = mcp_server.streamable_http_app()
    async with mcp_server.session_manager.run():
        yield
    scheduler.shutdown()
```

Remplacer le montage du router MCP (ligne 58 actuelle, `app.include_router(mcp_router)`) par :
```python
app.mount("/mcp", _mcp_asgi)
```
Ce montage référence le wrapper (fixe, construit une seule fois avec `app`), pas une app Starlette construite à l'avance — c'est ce qui permet à `lifespan` de reconstruire l'app MCP à chaque cycle sans jamais retoucher le routing d'`app`. Il doit rester le **dernier** ajout à `app` (après tous les `include_router` des autres routes) — un `Mount` Starlette capte tout le préfixe `/mcp/...`, le placer avant risquerait de masquer d'autres routes si l'ordre venait à changer.

**Point à vérifier empiriquement** (comme toujours pour un fait SDK/Starlette non prouvé dans ce projet avant ce jour) : que `scope["app"]` est bien injecté par Starlette dans l'appel du wrapper monté, et pointe vers l'instance `app` dont `state.mcp_app` a été peuplé par `lifespan`. Le Step 7 ci-dessous est la vérification réelle de ce point (si `_mcp_asgi` lève une `AttributeError`/`KeyError`, c'est que cette hypothèse est fausse — documenter ce qui a été trouvé et le mécanisme alternatif utilisé, ex. fermeture sur une variable mutable au lieu de `scope["app"]`).

- [ ] **Step 6: Ajouter la dépendance directe**

Dans `agent/requirements.txt`, ajouter une ligne après `claude-agent-sdk>=0.2.111` (ligne 5 actuelle) :
```
mcp>=1.23,<2.0
```

- [ ] **Step 7: Vérifier que l'app se construit toujours, y compris sur plusieurs cycles de lifespan dans le même process**

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_api.py -v`
Expected: PASS pour `test_health`, `test_ask_scoped_tenant`, `test_metrics_usage` (ces 3 ne touchent pas `/mcp`, ils prouvent que le `lifespan` corrigé tourne proprement sur plusieurs cycles `TestClient(app)` dans le même process — exactement le scénario qui cassait avec un singleton).

`test_mcp_requires_token`, `test_mcp_health_with_token`, `test_tenant_b_cannot_use_mcp_token_a` (3 tests de ce même fichier) vont encore échouer à ce stade : ils frappent l'ancienne forme REST (`POST /mcp/tools/get_project_health`), qui n'existe plus après ce remplacement complet — **attendu**, ce n'est pas une régression de cette tâche. Leur retrait fait partie de la Task 5 (portée étendue ci-dessous suite à cette découverte).

Run ensuite la suite complète pour confirmer que le problème de cascade (un cycle de lifespan qui échoue empêche `scheduler.shutdown()`, ce qui casse le test suivant via `ConflictingIdError` sur `alert_cycle`) a bien disparu :
Run: `PYTHONPATH=. .venv/bin/pytest tests/ -v`
Expected: seuls les tests frappant encore l'ancienne forme REST échouent (ceux listés ci-dessus, plus leurs équivalents dans `tests/integration/test_mcp_client.py` et `tests/isolation/test_non_fuite.py`) — zéro `RuntimeError`/`ConflictingIdError` en cascade, zéro ERROR sur des tests sans rapport avec MCP.

- [ ] **Step 8: Lint**

Run: `.venv/bin/ruff check agent/mcp/server.py agent/main.py tests/unit/test_mcp_server_assembly.py`
Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add agent/mcp/server.py agent/main.py agent/requirements.txt tests/unit/test_mcp_server_assembly.py
git commit -m "feat: assemble le vrai serveur MCP (FastMCP) et le monte dans l'app"
```

## Task 4 : Tests bout-en-bout (auth + transport réels)

**Files:**
- Create: `tests/integration/test_mcp_protocol.py`

**Interfaces:**
- Consumes : `agent.main.app` (Task 3, complet — mount + lifespan), le client MCP officiel (`mcp.ClientSession`, `mcp.client.streamable_http.streamablehttp_client`).
- Produces : rien de consommé par une tâche suivante — c'est la preuve empirique que l'auth + le transport fonctionnent réellement ensemble.

Cette tâche est la plus à risque du plan (cf. Global Constraints) : les valeurs exactes (URL de montage, forme de l'erreur sur token invalide) ne sont pas garanties à l'avance par la documentation du SDK. **Lancer chaque test individuellement dès qu'il est écrit** pour observer le comportement réel, plutôt que d'écrire les 3 tests d'un coup et espérer.

- [ ] **Step 1: Écrire la fixture serveur réel**

Créer `tests/integration/test_mcp_protocol.py`, en commençant par la fixture :

```python
"""Tests bout-en-bout du serveur MCP externe — auth + transport réels, vrai port TCP."""

import asyncio
import socket

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def mcp_base_url():
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
```

- [ ] **Step 2: Écrire et lancer le premier test (liste des outils avec un token valide)**

Ajouter :

```python
@pytest.mark.asyncio
async def test_valid_token_lists_four_tools(mcp_base_url):
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", mcp_token="tok-acme"))
        session.commit()

    async with streamablehttp_client(
        f"{mcp_base_url}/mcp", headers={"Authorization": "Bearer tok-acme"}
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.list_tools()

    names = {t.name for t in result.tools}
    assert names == {
        "get_project_health", "query_incidents", "get_business_kpis", "explain_anomaly",
    }
```

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_protocol.py::test_valid_token_lists_four_tools -v`

**Si ça échoue avec une 404 ou une erreur de connexion** : le chemin `f"{mcp_base_url}/mcp"` est probablement incorrect compte tenu de `streamable_http_path="/"` (Task 3) + `app.mount("/mcp", _mcp_asgi)`. Essayer `f"{mcp_base_url}/mcp/"` (slash final). Documenter dans le rapport la valeur qui fonctionne réellement — c'est un fait empirique à consigner, pas à deviner davantage.

Expected une fois résolu : PASS.

- [ ] **Step 3: Écrire et lancer le test du token invalide**

Ajouter :

```python
@pytest.mark.asyncio
async def test_invalid_token_rejected(mcp_base_url):
    with pytest.raises(Exception):
        async with streamablehttp_client(
            f"{mcp_base_url}/mcp", headers={"Authorization": "Bearer invalid-token"}
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as client:
                await client.initialize()
```

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_protocol.py::test_invalid_token_rejected -v`

Expected : PASS (une exception est bien levée). Si le type d'exception observé est identifiable et stable (ex. `httpx.HTTPStatusError` avec `status_code == 401`), resserrer l'assertion en conséquence dans le rapport plutôt que de garder un `Exception` générique — mais ne pas bloquer la tâche là-dessus si le type exact est ambigu/instable.

- [ ] **Step 4: Écrire et lancer le test de scoping tenant**

Ajouter :

```python
@pytest.mark.asyncio
async def test_tool_call_scoped_to_tenant(mcp_base_url):
    with get_session() as session:
        session.add(Tenant(id="alpha", name="Alpha", mcp_token="tok-alpha"))
        session.add(Tenant(id="beta", name="Beta", mcp_token="tok-beta"))
        session.commit()

    async def _kpis_for(token):
        async with streamablehttp_client(
            f"{mcp_base_url}/mcp", headers={"Authorization": f"Bearer {token}"}
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await client.call_tool("get_business_kpis", {"hours": 24})

    result_alpha = await _kpis_for("tok-alpha")
    result_beta = await _kpis_for("tok-beta")

    assert result_alpha.structuredContent["tenant_id"] == "alpha"
    assert result_beta.structuredContent["tenant_id"] == "beta"
```

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_protocol.py::test_tool_call_scoped_to_tenant -v`
Expected: PASS. Si `result_alpha.structuredContent` est `None`, vérifier `result_alpha.content` (bloc texte) à la place et adapter l'assertion — documenter dans le rapport laquelle des deux formes le SDK produit réellement pour un outil retournant un `dict[str, Any]`.

- [ ] **Step 5: Lancer le fichier complet**

Run: `PYTHONPATH=. .venv/bin/pytest tests/integration/test_mcp_protocol.py -v`
Expected: PASS — 3 tests verts.

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check tests/integration/test_mcp_protocol.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_mcp_protocol.py
git commit -m "test: couverture bout-en-bout du serveur MCP externe (auth + transport réels)"
```

## Task 5 : Retrait de la couverture REST obsolète

**Files:**
- Delete: `tests/integration/test_mcp_client.py`
- Modify: `tests/isolation/test_non_fuite.py`
- Modify: `tests/integration/test_api.py`

**Interfaces:**
- Consumes : rien de nouveau.
- Produces : rien de consommé par une tâche suivante.

**Contexte** : `tests/integration/test_mcp_client.py` testait exclusivement les anciens endpoints REST (`POST /mcp/tools/...`), qui n'existent plus après la Task 3 — ce fichier doit disparaître, remplacé par `tests/integration/test_mcp_protocol.py` (Task 4). `tests/isolation/test_non_fuite.py` contient 3 scénarios MCP sur les 10 scénarios obligatoires CI du fichier (`test_06_mcp_alpha_token`, `test_07_mcp_cross_tenant_forbidden`, `test_08_mcp_invalid_token`) qui frappent eux aussi l'ancien REST — à retirer. Leur couverture équivalente :
- `test_06_mcp_alpha_token` (token valide → 200, tenant_id correct) → couvert par `test_tool_call_scoped_to_tenant` (Task 4), en plus fort (compare 2 tenants).
- `test_08_mcp_invalid_token` (token invalide → erreur) → couvert par `test_invalid_token_rejected` (Task 4).
- `test_07_mcp_cross_tenant_forbidden` (incohérence `X-Tenant-ID` vs token → 403) → **aucun équivalent** : ce scénario testait un mécanisme (le header `X-Tenant-ID` comme double-contrôle) qui n'existe plus par design (§3 du design Phase 4 — le tenant vient uniquement du token désormais). Ce n'est pas une perte de couverture sur l'invariant réel (aucune fuite inter-tenant) : cet invariant reste couvert par `test_tool_call_scoped_to_tenant`.

**Découverte pendant l'implémentation de la Task 3** : `tests/integration/test_api.py` contient lui aussi 3 tests frappant l'ancien REST (`test_mcp_requires_token`, `test_mcp_health_with_token`, `test_tenant_b_cannot_use_mcp_token_a`, lignes 40-69 actuelles) — omis de l'inventaire initial de cette tâche. Même traitement, même couverture équivalente que ci-dessus (`test_mcp_requires_token`/`test_mcp_health_with_token` → `test_valid_token_lists_four_tools`/`test_tool_call_scoped_to_tenant` de la Task 4 ; `test_tenant_b_cannot_use_mcp_token_a` → aucun équivalent direct, même raison que `test_07_mcp_cross_tenant_forbidden` ci-dessus).

- [ ] **Step 1: Supprimer l'ancien fichier de test REST**

```bash
git rm tests/integration/test_mcp_client.py
```

- [ ] **Step 2: Retirer les 3 scénarios MCP obsolètes de `tests/isolation/test_non_fuite.py`**

Supprimer les fonctions `test_06_mcp_alpha_token`, `test_07_mcp_cross_tenant_forbidden`, `test_08_mcp_invalid_token` (lignes 83-116 actuelles) en entier. Retirer aussi `import re` (ligne 4) : confirmé que `re` n'est utilisé nulle part ailleurs dans ce fichier — ses deux seuls usages (`re.compile(...)` lignes 85 et 89) sont à l'intérieur de `test_06_mcp_alpha_token`, qui disparaît avec ce retrait.

Mettre à jour le docstring du module (ligne 1 actuelle) pour refléter le compte réel après retrait :
```python
"""Tests d'isolation multi-tenant — 7 scénarios obligatoires CI (MCP couvert séparément
dans tests/integration/test_mcp_protocol.py, protocole réel plutôt que REST)."""
```

- [ ] **Step 3: Retirer les 3 scénarios MCP obsolètes de `tests/integration/test_api.py`**

Supprimer les fonctions `test_mcp_requires_token`, `test_mcp_health_with_token`, `test_tenant_b_cannot_use_mcp_token_a` (lignes 40-69 actuelles) en entier. Retirer aussi `import re` (ligne 1) : ses deux seuls usages (`re.compile(...)`, lignes 47 et 51) sont à l'intérieur de `test_mcp_health_with_token`, qui disparaît avec ce retrait — confirmer qu'aucun autre test du fichier n'utilise `re` avant de le retirer.

- [ ] **Step 4: Lancer la suite complète**

Run: `PYTHONPATH=. .venv/bin/pytest tests/ -v`
Expected: PASS — aucune régression, `tests/isolation/test_non_fuite.py` a maintenant 7 tests (au lieu de 10), `tests/integration/test_api.py` a maintenant 3 tests (au lieu de 6), `tests/integration/test_mcp_client.py` n'existe plus.

- [ ] **Step 5: Lint**

Run: `.venv/bin/ruff check tests/isolation/test_non_fuite.py tests/integration/test_api.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add tests/isolation/test_non_fuite.py tests/integration/test_api.py
git commit -m "test: retire la couverture MCP REST obsolète (remplacée par test_mcp_protocol.py)"
```

## Task 6 : Documentation produit

**Files:**
- Modify: `docs/mcp-integration.md`
- Modify: `docs/runbooks/install-v2.md`

**Interfaces:** Aucune — documentation uniquement.

- [ ] **Step 1: Réécrire `docs/mcp-integration.md`**

Remplacer tout le contenu par :

```markdown
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
```

- [ ] **Step 2: Mettre à jour `docs/runbooks/install-v2.md`**

Remplacer la section « MCP (agents ETECH) » (lignes 31-37 actuelles) :

```markdown
## MCP (agents ETECH)

Vrai serveur MCP (protocole JSON-RPC, transport Streamable HTTP) monté sous `/mcp`, auth par `Authorization: Bearer <mcp_token>`.

Outils : `get_project_health`, `query_incidents`, `get_business_kpis`, `explain_anomaly`.

Voir [mcp-integration.md](../mcp-integration.md).
```

- [ ] **Step 3: Commit**

```bash
git add docs/mcp-integration.md docs/runbooks/install-v2.md
git commit -m "docs: met à jour la doc MCP pour le vrai serveur protocolaire"
```

## Task 7 : Régression finale + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:** Aucune (vérification finale + documentation).

- [ ] **Step 1: Suite complète**

Run: `PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v`
Expected: PASS — tous les tests (existants + Tasks 1-5), aucune régression.

- [ ] **Step 2: Lint global**

Run: `.venv/bin/ruff check agent/ tests/ cli/`
Expected: `All checks passed!`

- [ ] **Step 3: Ajouter une entrée CHANGELOG**

Ajouter sous la section `## [Unreleased]` existante de `CHANGELOG.md` :

```markdown
- Remplace le serveur MCP externe (`agent/mcp/server.py`) par un vrai serveur conforme au protocole MCP (SDK `mcp` officiel, `FastMCP`, transport Streamable HTTP JSON-RPC) — auparavant du REST FastAPI qui imitait seulement des noms d'outils MCP. Remplacement complet, pas de double-stack (aucun client externe réel n'existait encore, seulement un client de test simulant proto-factory). Auth par `TokenVerifier` custom réutilisant `Tenant.mcp_token` à l'identique ; le header `X-Tenant-ID` disparaît (le tenant vient uniquement du token, comme c'était déjà le cas en pratique).
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog pour le vrai serveur MCP externe (Phase 4)"
```
