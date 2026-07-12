"""Configuration centralisée de l'agent VIGIE."""

import os
from pathlib import Path

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
PROM_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo:3200")

MODEL_DIAGNOSTIC = os.environ.get("MODEL_DIAGNOSTIC", "claude-sonnet-4-6")
MODEL_TRIAGE = os.environ.get("MODEL_TRIAGE", "claude-haiku-4-5-20251001")

MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "8"))
MAX_LOG_LINES = int(os.environ.get("MAX_LOG_LINES", "200"))
MAX_PROM_RESULT_CHARS = int(os.environ.get("MAX_PROM_RESULT_CHARS", "8000"))

MOCK_LLM = os.environ.get("VIGIE_MOCK_LLM", "").lower() in ("1", "true", "yes")
API_TOKEN = os.environ.get("VIGIE_API_TOKEN", "")

DATA_DIR = Path(os.environ.get("VIGIE_DATA_DIR", "./data"))
DB_PATH = os.environ.get("VIGIE_DB_PATH", str(DATA_DIR / "vigie.db"))

ALERT_INTERVAL_MINUTES = int(os.environ.get("VIGIE_ALERT_INTERVAL_MINUTES", "10"))
SLACK_WEBHOOK_URL = os.environ.get("VIGIE_SLACK_WEBHOOK_URL", "")
SMTP_HOST = os.environ.get("VIGIE_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("VIGIE_SMTP_PORT", "587"))
SMTP_FROM = os.environ.get("VIGIE_SMTP_FROM", "")
SMTP_TO = os.environ.get("VIGIE_SMTP_TO", "")

DEFAULT_TENANT_ID = os.environ.get("VIGIE_DEFAULT_TENANT_ID", "default")
APP_VERSION = "3.0.1"
