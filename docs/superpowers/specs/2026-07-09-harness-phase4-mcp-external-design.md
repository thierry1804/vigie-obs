# VIGIE — Harness Phase 4 : vrai serveur MCP externe

Statut : Design validé | 2026-07-09
Réfère à : `docs/superpowers/specs/2026-07-07-architecture-agentique-harness-design.md` (design cible harness/multi-agents, §3.5), `docs/superpowers/harness-migration-status.md` (suivi global), `VIGIE_Specs_Cible.md` §3.5 (spec produit V2 du serveur MCP externe)

## 1. Contexte et motivation

Les Phases 1-3 ont construit le socle interne (harness multi-agents, presets, agent orchestrateur `ask`). Il reste un dernier morceau du design cible original (§3.5) : `agent/mcp/server.py`, qui expose aujourd'hui 4 « outils » via du **REST FastAPI classique** (`POST /mcp/tools/get_project_health`, `query_incidents`, `get_business_kpis`, `explain_anomaly`) plus un endpoint `GET /mcp/sse` qui ne fait qu'annoncer une liste statique de noms d'outils dans un flux SSE minimal — **rien de tout ça n'est le vrai protocole MCP** (pas de JSON-RPC, pas de négociation de session, pas de transport Streamable HTTP conforme).

Ce serveur est destiné à des agents tiers de l'écosystème ETECH (« proto-factory, agents métier futurs », `VIGIE_Specs_Cible.md:135`) qui consomment VIGIE comme un outil/capteur. **Constat fait pendant le brainstorming** : cette intégration réelle est encore *pending* — `docs/pending-external-validation.md:9` et `docs/mcp-integration.md:33-35` confirment qu'aucun client externe réel n'existe encore, seul un client de test qui simule son comportement (`tests/integration/test_mcp_client.py`, docstring : « Test client MCP simulant proto-factory »). Ce constat réduit le risque d'un remplacement complet par rapport à ce qu'anticipait le design original (qui prévoyait de traiter ce changement de transport avec prudence à cause d'un « client réel à coordonner » — client qui, en pratique, n'existe pas encore dans ce dépôt ni ailleurs de façon accessible).

**Décision retenue avec l'utilisateur** : remplacement complet, pas de double-stack. Les 4 endpoints REST et `/mcp/sse` disparaissent, remplacés uniquement par un vrai serveur MCP protocolaire construit avec le SDK Python officiel `mcp` (déjà présent en dépendance transitive de `claude-agent-sdk`, version installée 1.28.1 — sera ajouté en dépendance directe d'`agent/requirements.txt`).

## 2. Architecture

```
agent/mcp/
  server.py     Construit le FastMCP, monte les 4 outils, expose build_mcp_app() -> Starlette
                (via server.streamable_http_app()) — point d'entrée unique pour agent/main.py.
  auth.py       NOUVEAU — VigieTokenVerifier(TokenVerifier) : verify_token(token) consulte
                Tenant.mcp_token (même requête qu'aujourd'hui), retourne un AccessToken
                avec claims={"tenant_id": tenant.id} si trouvé, None sinon (401 automatique
                via RequireAuthMiddleware du SDK). Remplace verify_mcp_token() et le header
                X-Tenant-ID — qui n'était qu'une vérification de cohérence optionnelle, le
                tenant a toujours été dérivé du token lui-même (comportement préservé).
  tools.py      NOUVEAU — les 4 handlers (get_project_health, query_incidents,
                get_business_kpis, explain_anomaly), logique métier inchangée à l'identique,
                déplacés depuis l'actuel agent/mcp/server.py. Chaque handler lit le tenant_id
                via get_access_token().claims["tenant_id"]
                (mcp.server.auth.middleware.auth_context.get_access_token()).

agent/main.py   ~ remplace `app.include_router(mcp_router)` par
                  `app.mount("/mcp", build_mcp_app())` ; le lifespan existant (lignes 34-47,
                  gère déjà init_db()/APScheduler) englobe désormais aussi le cycle de vie du
                  StreamableHTTPSessionManager exposé par l'app MCP.

Supprimé : /mcp/sse (remplacé par la négociation tools/list native du protocole JSON-RPC),
           les 4 modèles Pydantic (HealthParams/IncidentsParams/KpiParams/ExplainParams —
           remplacés par les schémas d'entrée des @server.tool(), inférés des signatures
           Python typées).
```

**Choix de l'API SDK** : `FastMCP` (API haut niveau du paquet `mcp`, décorateur `@server.tool()`) plutôt que `mcp.server.lowlevel.Server` (contrôle bas niveau, tout le boilerplate JSON-RPC à la charge du code VIGIE — pas justifié pour 4 outils à requête/réponse standard) ou qu'un shim JSON-RPC maison sans le SDK (réinventerait la négociation de session/SSE/formes d'erreur du protocole — contraire à l'objectif même de cette phase).

## 3. Auth (`agent/mcp/auth.py`)

```python
from mcp.server.auth.provider import AccessToken, TokenVerifier
from agent.db.models import Tenant
from agent.db.session import get_session


class VigieTokenVerifier(TokenVerifier):
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

Branché via `FastMCP(auth=AuthSettings(...), token_verifier=VigieTokenVerifier())` — noms de champs exacts (`AuthSettings`, paramètres de `FastMCP.__init__`) à **vérifier contre le SDK réel pendant l'implémentation**, pas supposés depuis cette doc (cohérent avec la pratique du projet, `docs/superpowers/harness-migration-status.md` §4 : tout fait SDK est confirmé par sondage réel, jamais deviné).

Le SDK gère nativement le 401 (token absent/invalide) via `RequireAuthMiddleware` — pas de `HTTPException` à lever à la main, contrairement à l'actuel `verify_mcp_token()`.

Chaque tool récupère le tenant via une fonction partagée :
```python
from mcp.server.auth.middleware.auth_context import get_access_token

def _current_tenant_id() -> str:
    access_token = get_access_token()
    return access_token.claims["tenant_id"]
```

Ce mécanisme repose sur une `contextvars.ContextVar` posée par `AuthContextMiddleware` avant l'appel du handler — propagée automatiquement le long de la chaîne d'`await` d'une même tâche asyncio (confirmé par lecture directe de `mcp/server/auth/middleware/auth_context.py` pendant le brainstorming). C'est le mécanisme canonique du SDK (`Context` lui-même n'expose pas de propriété `.auth`/`.claims` dédiée — son docstring renvoie explicitement à `get_access_token()`).

**Différence de comportement assumée** : plus de notion de header `X-Tenant-ID` séparé — le tenant vient uniquement du token, ce qui est déjà la source de vérité aujourd'hui (le header n'était qu'un double-contrôle optionnel qui provoquait un 403 en cas d'incohérence, jamais la source réelle du tenant_id retourné). Comportement fonctionnellement équivalent pour tout appelant qui envoyait un `X-Tenant-ID` cohérent (le cas normal) ; un appelant qui misait sur l'incohérence pour être bloqué perdrait ce garde-fou spécifique — mais aucun test ni doc actuel ne documente ce cas comme un mécanisme de sécurité voulu au-delà du token lui-même.

## 4. Les 4 outils (`agent/mcp/tools.py`)

```python
from mcp.server.fastmcp import FastMCP

def register_tools(server: FastMCP) -> None:
    @server.tool()
    async def get_project_health(hours: float = 24) -> dict:
        tenant_id = _current_tenant_id()
        errors = await run_query_loki('{level="error"}', hours_back=hours, limit=20, tenant_id=tenant_id)
        cpu = await run_query_prometheus('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)')
        taxonomy = load_taxonomy(tenant_id)
        business_types = [e["name"] for e in (taxonomy or {}).get("events", [])]
        return {
            "tenant_id": tenant_id, "window_hours": hours,
            "technical": {"errors_sample": errors[:500], "cpu_query": cpu[:300]},
            "business": {"event_types": business_types},
            "status": "degraded" if "error" in errors.lower()[:100] else "ok",
        }

    @server.tool()
    async def query_incidents(hours: float = 168, status: str | None = None) -> dict:
        tenant_id = _current_tenant_id()
        since = datetime.utcnow() - timedelta(hours=hours)
        with get_session() as session:
            q = session.query(Anomaly).filter(Anomaly.tenant_id == tenant_id, Anomaly.created_at >= since)
            if status:
                q = q.filter(Anomaly.status == status)
            rows = q.order_by(Anomaly.created_at.desc()).limit(50).all()
        return {
            "incidents": [
                {"id": r.id, "status": r.status, "title": r.title, "rule_name": r.rule_name,
                 "created_at": r.created_at.isoformat()}
                for r in rows
            ]
        }

    @server.tool()
    async def get_business_kpis(hours: float = 24) -> dict:
        tenant_id = _current_tenant_id()
        taxonomy = load_taxonomy(tenant_id)
        kpis = {}
        if taxonomy:
            for ev in taxonomy.get("events", []):
                name = ev["name"]
                result = await run_query_loki(
                    f'{{business_event_type="{name}"}}', hours_back=hours, limit=5, tenant_id=tenant_id,
                )
                count = max(0, result.count("\n") + (1 if result and "Aucun" not in result else 0))
                kpis[name] = {"sample_lines": count, "description": ev.get("description", "")}
        return {"tenant_id": tenant_id, "window_hours": hours, "kpis": kpis}

    @server.tool()
    async def explain_anomaly(anomaly_id: int | None = None, question: str | None = None) -> dict:
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
            "ask", f"Investigation structurée (FAITS/HYPOTHÈSES):\n{context}",
            tenant_id=tenant_id, endpoint="mcp/explain_anomaly",
        )
        return {"tenant_id": tenant_id, "diagnosis": diagnosis}
```

Logique métier de chaque outil **inchangée à l'identique** par rapport à l'actuel `agent/mcp/server.py` (lignes 39-105 pour les 3 handlers directs, 108-129 pour `explain_anomaly`) — seule la façade change : paramètres de fonction directs et typés (FastMCP en dérive le schéma JSON automatiquement) au lieu de modèles Pydantic + `Depends(verify_mcp_token)`, et `tenant_id` lu via `_current_tenant_id()` au lieu d'être injecté par FastAPI. `explain_anomaly` reste le seul outil qui appelle `run_agent("ask", ...)` — les 3 autres restent des handlers directs sans LLM, conforme au design cible §3.5/§4.

## 5. Lifespan (`agent/main.py`)

Le `StreamableHTTPSessionManager` exposé par l'app MCP a son propre cycle de vie asynccontextmanager, à intégrer dans le `lifespan` existant (à côté de `init_db()`/APScheduler) :

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    async with mcp_app.session_manager.run():  # nouveau — nom d'attribut à vérifier contre le SDK réel
        scheduler.start()
        yield
        scheduler.shutdown()
```

## 6. Gestion d'erreurs

Aucun changement de logique dans les 4 handlers eux-mêmes : mêmes chemins d'erreur qu'aujourd'hui (Loki en erreur → chaîne d'erreur retournée telle quelle dans le champ concerné, `anomaly_id` introuvable ou d'un autre tenant → contexte par défaut, silencieux comme avant). Le SDK gère nativement les erreurs de protocole (token invalide → 401 par `RequireAuthMiddleware`, nom d'outil inconnu → erreur JSON-RPC standard) — rien à coder à la main pour ça, contrairement à l'actuel `verify_mcp_token()` qui levait des `HTTPException` explicites (401/403).

## 7. Tests

Constat technique confirmé pendant le brainstorming (lecture directe du paquet `mcp` installé) : le SDK n'offre **aucun raccourci officiel** pour tester le transport Streamable HTTP + le pipeline d'auth Starlette (`AuthenticationMiddleware`/`AuthContextMiddleware`/`RequireAuthMiddleware`) sans un vrai port TCP — contrairement au `TestClient` FastAPI in-process utilisé partout ailleurs dans ce repo. Le seul utilitaire de test officiel du SDK (`mcp.shared.memory.create_connected_server_and_client_session`) court-circuite entièrement cette pile HTTP/auth pour parler directement au protocole bas niveau. **Décision retenue** : un vrai serveur `uvicorn` en thread pour la poignée de tests qui valident spécifiquement auth+transport ; appel direct des fonctions Python pour la logique de chaque outil (pas besoin du protocole pour ça).

```
tests/unit/test_mcp_auth.py       NOUVEAU — VigieTokenVerifier.verify_token() : token valide
                                   → AccessToken avec le bon tenant_id ; token inconnu → None.
                                   Appel direct de la fonction Python.

tests/unit/test_mcp_tools.py      NOUVEAU — logique des 4 handlers testée par appel direct
                                   (même pattern que tests/unit/test_mcp_server_tools.py pour
                                   les outils internes vigie-obs) — httpx_mock pour
                                   Loki/Prometheus, pas de client MCP ni de serveur réel.
                                   tenant_id fourni directement à la fonction testée (extraction
                                   via _current_tenant_id() isolée du reste de la logique).

tests/integration/test_mcp_protocol.py   NOUVEAU — tests bout-en-bout :
  - fixture qui lance build_mcp_app() sous uvicorn dans un thread (port éphémère local),
    teardown propre du thread en fin de test
  - test_valid_token_lists_tools : streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"})
    → ClientSession.initialize() + list_tools() → les 4 noms d'outils présents
  - test_invalid_token_rejected : token bidon → échec de négociation (401)
  - test_tool_call_scoped_to_tenant : deux tenants avec tokens distincts, chacun appelle
    get_business_kpis via le protocole réel, vérifie que le tenant_id retourné correspond
    au token utilisé — couvre l'invariant multi-tenant pour ce serveur

tests/integration/test_mcp_client.py   SUPPRIMÉ — testait le REST actuel, qui disparaît
                                        avec cette phase ; remplacé par test_mcp_protocol.py
tests/isolation/test_non_fuite.py      ~ les scénarios MCP existants (test_mcp_requires_token,
                                        test_mcp_health_with_token, scénarios cross-tenant
                                        alpha/beta sur /mcp) migrent vers test_mcp_protocol.py,
                                        où ils ont plus de sens (protocole réel testé bout en
                                        bout) ; les scénarios non-MCP du fichier restent
                                        inchangés.
```

## 8. Documentation produit

Dans le périmètre de cette phase (décision explicite — une doc fausse dès le merge est pire qu'une doc absente) :
- `docs/mcp-integration.md` : remplacer les exemples curl REST par un exemple client MCP (extrait Python `streamablehttp_client` + `ClientSession`, ou a minima la séquence de négociation JSON-RPC équivalente) ; la section « proto-factory pending » (lignes 33-35) est conservée mais mise à jour pour refléter que le serveur est désormais conforme au protocole (ce qui était bloquant côté VIGIE ne l'est plus — seul l'accès réel à proto-factory reste pending).
- `docs/runbooks/install-v2.md:31-37` : vérifier qu'aucun détail REST n'y est dupliqué localement (le contenu renvoie déjà vers `mcp-integration.md`) ; mettre à jour si besoin.

## 9. Dépendances

`agent/requirements.txt` gagne une entrée directe pour `mcp` (aujourd'hui présent uniquement en transitif via `claude-agent-sdk`, qui impose `mcp<2.0.0,>=1.23.0`) — version à pinner dans cette fourchette, cohérente avec la version déjà installée (1.28.1).

## 10. Hors périmètre de cette phase

- Scopes OAuth granulaires par outil : tout token garde l'accès aux 4 outils, comme aujourd'hui — pas de permission fine par tenant/outil, ça n'existait pas avant non plus.
- Intégration réelle avec proto-factory : reste *pending* côté client externe (hors du contrôle de ce dépôt) — cette phase rend VIGIE conforme au protocole, elle ne dépend d'aucun accès réel à proto-factory pour être complète.
- Isolation multi-tenant physique (SQLite partagé) : reste une défense en profondeur applicative, cohérent avec le hors-périmètre du design cible §8 — inchangé par cette phase.
