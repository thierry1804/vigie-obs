# VIGIE — Spécifications techniques V0

**Agent d'observabilité branchable — technique & fonctionnelle**
Statut : Prototype | Version : 0.1.0 | Juillet 2026

---

## 1. Vue d'ensemble

VIGIE V0 est un stack Docker Compose de 5 services, conçu pour s'instrumenter sur un projet web existant sans modification du code applicatif. La collecte se fait aux frontières universelles (fichiers de logs, métriques système) ; un agent LLM expose un diagnostic conversationnel au-dessus des données collectées.

```
Projet observé ──► Vector ──► Loki ──┐
   (logs,           │                ├──► Agent LLM ──► /ask, /report/daily
    métriques)       │                │      (FastAPI + Anthropic SDK)
                     └──► Prometheus ─┘
                                │
                                ▼
                            Grafana (dashboards)
```

---

## 2. Composants et versions

| Service | Image | Rôle | Port |
|---|---|---|---|
| Vector | `timberio/vector:0.43.X-alpine` | Collecte, normalisation, masquage PII | interne |
| Loki | `grafana/loki:3.3.0` | Stockage des logs | interne |
| node-exporter | `prom/node-exporter:v1.8.2` | Métriques système hôte | interne |
| Prometheus | `prom/prometheus:v3.1.0` | Stockage des métriques | interne |
| Grafana | `grafana/grafana:11.4.0` | Dashboards | `3000` |
| Agent VIGIE | Python 3.12 / FastAPI (custom) | Diagnostic conversationnel + rapports | `8080` |

Réseau Docker dédié (`vigie`), tous les services communiquent en interne sauf Grafana et l'Agent, exposés sur l'hôte.

---

## 3. Couche collecte — Vector (`config/vector.toml`)

### 3.1 Sources

| Source | Chemin | Commentaire |
|---|---|---|
| `web_server` | `/host/var/log/nginx/*.log`, `/host/var/log/apache2/*.log` | Présent sur ~tout projet web |
| `app_logs` | `/host/app/log/*.log` | **À adapter par projet** (Symfony `var/log`, Laravel `storage/logs`, etc.) |
| `docker_logs` | `/host/containers/*/*-json.log` | Si le projet observé est lui-même dockerisé |

`ignore_older_secs = 86400` sur toutes les sources (on ne réingère pas l'historique complet à chaque redémarrage).

### 3.2 Transforms

**`normalize`** (remap VRL) :
- Parsing JSON best-effort (`parse_json`), fusion si succès
- Sinon, inférence du niveau de log par recherche de sous-chaînes : `ERROR`/`CRITICAL`/`error` → `error`, `WARN` → `warning`, sinon `info`
- Masquage PII : regex `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` → `<email-masqué>`
- Tag `projet` (constante à date, prépare le multi-tenant)

**`business_events`** (remap VRL) :
- Classification `stream_type` par mots-clés sur le message en minuscules : `created`, `créé`, `commande`, `order`, `facture`, `invoice`, `payment`, `paiement` → `business` ; sinon `technical`
- Heuristique volontairement naïve en V0 — l'apprentissage de patterns est prévu en V1

### 3.3 Sink

Vers Loki, encodage JSON, labels indexés : `projet`, `level`, `stream_type`.

---

## 4. Couche métriques — Prometheus (`config/prometheus.yml`)

- `scrape_interval: 15s`
- Cible unique en V0 : `node-exporter:9100` (CPU, mémoire, disque, load average de l'hôte)
- Section commentée prête à activer si l'application observée expose déjà un endpoint `/metrics` (reste optionnel, pour ne pas casser la promesse « zéro code »)

---

## 5. Agent LLM (`agent/main.py`)

### 5.1 Stack

- FastAPI + Uvicorn
- SDK `anthropic` (Python)
- `httpx` pour les appels vers Loki/Prometheus

### 5.2 Modèles et routage

| Variable d'env | Valeur par défaut | Usage |
|---|---|---|
| `MODEL_DIAGNOSTIC` | `claude-sonnet-4-6` | Analyse, corrélation, rédaction de rapports — seul modèle réellement appelé en V0 |
| `MODEL_TRIAGE` | `claude-haiku-4-5-20251001` | Réservé à la classification/filtrage en continu — **non utilisé en V0**, prévu pour le triage d'anomalies en V1 |

### 5.3 Garde-fous de budget

- `MAX_TOOL_TURNS = 8` : nombre maximum d'allers-retours outils par requête agentique
- `MAX_LOG_LINES = 200` : troncature des résultats Loki avant injection dans le contexte du modèle
- Résultats Prometheus tronqués à 8000 caractères
- Le LLM n'a jamais accès au flux brut de logs — uniquement aux résultats de requêtes LogQL/PromQL qu'il formule lui-même

### 5.4 Outils exposés au modèle (tool use)

**`query_loki`**
```json
{
  "logql": "string (requis)",
  "hours_back": "number (défaut 24)",
  "limit": "integer (défaut 100, plafonné à 200)"
}
```
Exécute une requête `query_range` contre l'API Loki, formate chaque ligne en `[timestamp] level | message`.

**`query_prometheus`**
```json
{
  "promql": "string (requis)",
  "range_hours": "number (optionnel — si absent, requête instantanée ; si présent, requête de plage avec step auto ~100 points)"
}
```

### 5.5 Boucle agentique

Méthode imposée par le prompt système : **Plan → Exécute → Vérifie**.
1. Formuler une hypothèse et les requêtes qui la testeraient
2. Lancer les requêtes (large puis affiné)
3. Avant de conclure, challenger l'hypothèse : chercher une explication alternative non exclue par les données, et la tester si besoin

Le prompt système impose aussi :
- Distinction explicite faits observés / hypothèses
- Aveu d'insuffisance de données plutôt que d'invention, avec proposition d'instrumentation complémentaire
- Réponses en français, concises, actionnables

Boucle technique : appel `messages.create` avec `tools=TOOLS` → si `stop_reason == "tool_use"`, exécution des tool calls et ré-injection des `tool_result` → répétition jusqu'à réponse texte finale ou épuisement de `MAX_TOOL_TURNS` (message de repli explicite si budget épuisé).

### 5.6 Endpoints API

| Endpoint | Méthode | Entrée | Sortie | Fonction |
|---|---|---|---|---|
| `/ask` | POST | `{"question": "string"}` | `{"answer": "string"}` | Diagnostic conversationnel libre |
| `/report/daily` | GET | — | `{"date": "...", "report": "..."}` | Rapport 24h : santé technique, activité métier inférée, points d'attention |
| `/health` | GET | — | `{"status": "ok", "service": "vigie-agent"}` | Healthcheck |
| `/docs` | GET | — | Swagger UI | Documentation interactive auto-générée |

---

## 6. Configuration et déploiement

### 6.1 Variables d'environnement (`.env`)

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Oui | — | Clé API Anthropic |
| `GRAFANA_PASSWORD` | Non | `vigie-admin` | Mot de passe admin Grafana |

### 6.2 Fichiers à adapter par projet

Un seul fichier change réellement d'un déploiement à l'autre :

- `config/vector.toml` — chemins des logs sources (section `[sources]`)

Le reste (`docker-compose.yml`, `config/prometheus.yml`, `config/grafana-datasources.yml`, le code de l'agent) est identique quel que soit le projet observé.

### 6.3 Commande de démarrage

```bash
cp .env.example .env          # renseigner ANTHROPIC_API_KEY
# adapter config/vector.toml (chemins de logs du projet observé)
docker compose up -d --build
```

### 6.4 Accès

- Grafana : `http://localhost:3000` (login `admin` / `GRAFANA_PASSWORD`)
- Agent (Swagger) : `http://localhost:8080/docs`

### 6.5 Volumes persistants

| Volume | Contenu |
|---|---|
| `loki-data` | Logs indexés |
| `prom-data` | Séries temporelles de métriques |
| `grafana-data` | Dashboards, datasources, configuration Grafana |

---

## 7. Sécurité et souveraineté des données

- Déploiement 100 % self-hosted — aucune donnée ne quitte l'infrastructure du client
- Seul flux réseau sortant : appels à `api.anthropic.com` (contenu des `tool_result`, potentiellement des extraits de logs déjà masqués PII)
- Masquage des emails à la collecte, avant tout stockage
- Aucune authentification anonyme sur Grafana (`GF_AUTH_ANONYMOUS_ENABLED=false`)
- Recommandation : restreindre `config/vector.toml` aux logs strictement nécessaires ; éviter de monter des répertoires contenant des secrets (`.env`, clés privées)

---

## 8. Dépendances

**Infrastructure** : Docker, Docker Compose

**Agent (`agent/requirements.txt`)** :
```
fastapi>=0.115
uvicorn[standard]>=0.32
httpx>=0.27
anthropic>=0.40
pydantic>=2.9
```

**Réseau sortant requis** : accès à `api.anthropic.com` uniquement

---

## 9. Limites assumées de la V0

| Limite | Détail | Traitement prévu |
|---|---|---|
| Pas de tracing intra-applicatif | On ne voit pas quelle fonction est lente à l'intérieur du code | SDK OpenTelemetry optionnel ou eBPF — V2 |
| Inférence métier dépendante de la verbosité des logs | Si l'appli logge peu, peu d'événements métier détectables | L'agent peut recommander des points de log ciblés (posture conseil) |
| Classification `stream_type` par mots-clés fixes | Pas d'apprentissage des patterns propres au projet | Apprentissage par l'agent — V1 |
| Mono-projet | Le label `projet` est une constante, pas encore un vrai multi-tenant | Multi-tenant complet — V1/V2 |
| Rétention Loki par défaut | Non dimensionnée pour un volume de production élevé | À calibrer selon volumétrie réelle lors du déploiement pilote |
| Coût LLM par appel explicite | Chaque `/ask` et chaque rapport consomme des tokens Sonnet | Filtrage classique en amont (Vector/Loki), LLM seulement sur anomalies/rapports — pattern déjà en place |

---

## 10. Roadmap (rappel)

| Version | Contenu |
|---|---|
| **V0** (ce document) | Collecte zéro-code + normalisation + agent conversationnel + rapport quotidien |
| **V1** | Découverte automatique des formats de logs, événements métier appris (non figés par mots-clés), alerting Slack/mail en langage naturel, triage `MODEL_TRIAGE` (Haiku) → escalade `MODEL_DIAGNOSTIC` (Sonnet) sur anomalies détectées |
| **V2** | SDK OTel optionnel (tracing profond), vrai multi-tenant, serveur MCP exposant `get_project_health`, `query_incidents`, `get_business_kpis` aux autres agents ETECH |
