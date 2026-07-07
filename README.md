# VIGIE — Agent d'observabilité branchable (V0)

Observabilité technique **et** fonctionnelle sur un projet existant, **sans modifier le code applicatif**. L'agent se branche aux frontières universelles (logs, métriques système, trafic) et expose un diagnostic conversationnel propulsé par LLM.

## Architecture

```
                         ┌─────────────────────────────────────┐
  Projet observé         │              VIGIE                  │
  (n'importe quelle      │                                     │
   stack)                │  Vector ──► Loki ◄──┐               │
     │ logs fichiers ───►│  (collecte  (logs)  │               │
     │ logs conteneurs ─►│   + normali-        ├──► Agent LLM  │
     │ métriques OS ────►│   sation    Prome-  │    (FastAPI + │
                         │   + masquage theus ◄┘     tool use) │
                         │   PII)      (métriques)      │      │
                         │                │             │      │
                         │                ▼             ▼      │
                         │             Grafana      /ask       │
                         │           (dashboards)  /report     │
                         └─────────────────────────────────────┘
```

**Principe clé** : la seule chose qui change d'un projet à l'autre est `config/vector.toml` (chemins des logs). Tout le reste est identique. C'est ce qui rend l'agent "branchable".

## Démarrage

```bash
cp .env.example .env          # renseigner ANTHROPIC_API_KEY
# adapter les chemins de logs dans config/vector.toml et docker-compose.yml
docker compose up -d --build
```

- Grafana : http://localhost:3000 (admin / mot de passe du .env)
- Agent : http://localhost:8080/docs (Swagger)

## Utilisation de l'agent

```bash
# Diagnostic conversationnel
curl -X POST http://localhost:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Pourquoi y a-t-il eu un pic d'erreurs 500 cette nuit ?"}'

# Rapport quotidien (à brancher sur un cron / n8n / Slack webhook)
curl http://localhost:8080/report/daily
```

## Ce que fait la V0

- **Collecte zéro-code** : logs serveur web, logs applicatifs, logs conteneurs, métriques système.
- **Normalisation** : format pivot JSON, niveau de log inféré, masquage PII (emails) à la collecte.
- **Pré-classification métier** : heuristique par mots-clés (`stream_type=business`) — c'est volontairement naïf, la V1 confie cet apprentissage à l'agent.
- **Agent diagnostic** : boucle agentique Plan-Exécute-Vérifie avec Loki et Prometheus comme outils, garde-fous de budget (8 tours max, résultats tronqués).
- **Rapport quotidien** : santé technique + activité métier inférée.

## Maîtrise du coût LLM

Le LLM n'analyse **jamais** le flux brut : les logs transitent par Vector/Loki (règles classiques, coût nul). Le modèle n'est appelé que :
1. À la demande (`/ask`) ;
2. Sur rapport programmé (`/report/daily`) ;
3. (V1) Sur anomalie détectée par seuils — triage par Haiku, escalade Sonnet.

## Roadmap

| Version | Contenu |
|---------|---------|
| **V0** (ce dépôt) | Collecte + normalisation + agent conversationnel + rapport quotidien |
| **V1** | Découverte automatique des formats de logs (l'agent génère les transforms Vector), extraction d'événements métier apprise, alerting Slack en langage naturel, boucle anomalie Haiku→Sonnet |
| **V2** | SDK OTel optionnel (tracing profond), serveur MCP exposant `get_project_health`, `query_incidents`, `get_business_kpis` aux autres agents de la chaîne |

## Limites assumées (V0)

- Pas de tracing intra-applicatif (nécessite SDK ou eBPF — V2).
- L'inférence métier dépend de la verbosité des logs existants.
- Rétention Loki par défaut (à dimensionner selon le volume du projet).
- Mono-projet ; le label `projet` prépare le multi-tenant.
