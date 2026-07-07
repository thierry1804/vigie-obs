# VIGIE — Agent d'observabilité branchable

Observabilité technique **et** fonctionnelle sur un projet existant, **sans modifier le code applicatif** (SDK OTel optionnel en V2).

## Versions

| Version | Tag | Contenu |
|---|---|---|
| V0 | 0.1.0 | Collecte + agent réactif |
| V1 | 1.0.0 | Discovery, taxonomie, alerting, multi-tenant partiel |
| V2 | 2.0.0 | + Tempo, MCP, SDK OTel, isolation complète |

## Démarrage rapide

```bash
cp .env.example .env
docker compose up -d --build
```

- Grafana : http://localhost:3000
- Agent : http://localhost:8080/docs

## CLI

```bash
PYTHONPATH=. python -m cli discover ./lab/stacks/symfony -o vector.proposed.toml
PYTHONPATH=. python -m cli taxonomy propose --tenant default
```

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r agent/requirements.txt
PYTHONPATH=. VIGIE_MOCK_LLM=1 .venv/bin/pytest tests/ -v
```

## Documentation

- [Install V1](docs/runbooks/install-v1.md)
- [Install V2](docs/runbooks/install-v2.md)
- [Intégration MCP](docs/mcp-integration.md)
- [Validations externes pending](docs/pending-external-validation.md)
- [CHANGELOG](CHANGELOG.md)
