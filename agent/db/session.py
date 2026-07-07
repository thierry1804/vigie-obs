"""Gestion de session SQLite."""

import os
from contextlib import contextmanager

from agent.config import DEFAULT_TENANT_ID, DATA_DIR
from agent.db.models import AlertRule, Base, Tenant, make_engine, make_session_factory

_session_factory = None


def init_db(db_path: str | None = None) -> None:
    global _session_factory
    path = db_path or os.environ.get("VIGIE_DB_PATH", str(DATA_DIR / "vigie.db"))
    engine = make_engine(path)
    Base.metadata.create_all(engine)
    _session_factory = make_session_factory(path)
    _seed_defaults()


def _seed_defaults() -> None:
    with get_session() as session:
        if session.get(Tenant, DEFAULT_TENANT_ID) is None:
            session.add(
                Tenant(
                    id=DEFAULT_TENANT_ID,
                    name="Default",
                    budget_llm_tokens=500_000,
                    api_token="default-api-token",
                    mcp_token="default-mcp-token",
                )
            )
            session.commit()
        if session.query(AlertRule).count() == 0:
            defaults = [
                AlertRule(
                    tenant_id=DEFAULT_TENANT_ID,
                    name="error_rate",
                    rule_type="logql",
                    query='sum(count_over_time({level="error"}[5m]))',
                    threshold=10.0,
                    cooldown_minutes=60,
                ),
                AlertRule(
                    tenant_id=DEFAULT_TENANT_ID,
                    name="cpu_high",
                    rule_type="promql",
                    query='100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
                    threshold=90.0,
                    cooldown_minutes=60,
                ),
                AlertRule(
                    tenant_id=DEFAULT_TENANT_ID,
                    name="disk_low",
                    rule_type="promql",
                    query='(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100',
                    threshold=10.0,
                    cooldown_minutes=120,
                ),
            ]
            session.add_all(defaults)
            session.commit()


@contextmanager
def get_session():
    if _session_factory is None:
        init_db()
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()
