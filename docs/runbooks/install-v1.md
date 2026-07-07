# Installation VIGIE V1 (< 1 jour)

## Prérequis

- Docker + Docker Compose
- Accès aux logs du projet observé (chemins montables en read-only)

## Étapes

1. Cloner le dépôt et copier `.env.example` vers `.env`
2. Lancer la discovery :
   ```bash
   PYTHONPATH=. VIGIE_MOCK_LLM=0 python -m cli discover /chemin/projet --output config/vector.toml.proposed
   ```
3. Valider et appliquer la config Vector proposée
4. Démarrer la stack :
   ```bash
   docker compose up -d --build
   ```
5. Proposer et valider la taxonomie métier :
   ```bash
   python -m cli taxonomy propose --tenant default
   python -m cli taxonomy validate --tenant default
   python -m cli taxonomy apply --tenant default
   ```
6. Configurer alerting : `POST /alerts/config` (Swagger : http://localhost:8080/docs)

## Accès

- Grafana : http://localhost:3000
- Agent : http://localhost:8080/docs
