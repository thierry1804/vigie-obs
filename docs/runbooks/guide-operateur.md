# Guide opérateur VIGIE

## Workflow discover → validate → up

1. **Discovery**
   ```bash
   PYTHONPATH=. python -m cli discover /chemin/projet -o config/vector.toml.proposed
   ```
2. **Validation** — relire le diff, ajuster si nécessaire
3. **Application** — remplacer `config/vector.toml` par la version validée
4. **Démarrage** — `docker compose up -d --build`
5. **Taxonomie** (après quelques jours de collecte)
   ```bash
   PYTHONPATH=. python -m cli taxonomy propose --tenant default
   PYTHONPATH=. python -m cli taxonomy validate --tenant default
   PYTHONPATH=. python -m cli taxonomy apply --tenant default
   ```
6. **Alerting** — configurer via `POST /alerts/config` ou Swagger

## Vérifications

- `GET /health` — agent OK
- `GET /metrics/usage` — consommation tokens
- Grafana — dashboards événements métier
