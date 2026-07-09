"""Tests d'isolation multi-tenant — 7 scénarios obligatoires CI (MCP couvert séparément
dans tests/integration/test_mcp_protocol.py, protocole réel plutôt que REST)."""

import os

import pytest
from fastapi.testclient import TestClient

from agent.db.models import Anomaly, Tenant
from agent.db.session import get_session
from agent.main import app


@pytest.fixture
def isolated_client():
    os.environ["VIGIE_API_TOKEN"] = "master-token"
    with get_session() as session:
        session.add(Tenant(id="alpha", name="Alpha", api_token="tok_alpha", mcp_token="mcp_alpha"))
        session.add(Tenant(id="beta", name="Beta", api_token="tok_beta", mcp_token="mcp_beta"))
        session.add(
            Anomaly(tenant_id="alpha", signature="sig_a", title="A only", status="open")
        )
        session.add(
            Anomaly(tenant_id="beta", signature="sig_b", title="B only", status="open")
        )
        session.commit()
    with TestClient(app) as c:
        yield c
    os.environ["VIGIE_API_TOKEN"] = ""


def test_01_ask_scoped_alpha(isolated_client):
    r = isolated_client.post(
        "/ask",
        json={"question": "q"},
        headers={"X-Tenant-ID": "alpha", "Authorization": "Bearer master-token"},
    )
    assert r.json()["tenant_id"] == "alpha"


def test_02_ask_scoped_beta(isolated_client):
    r = isolated_client.post(
        "/ask",
        json={"question": "q"},
        headers={"X-Tenant-ID": "beta", "Authorization": "Bearer master-token"},
    )
    assert r.json()["tenant_id"] == "beta"


def test_03_alerts_history_alpha_no_beta(isolated_client):
    r = isolated_client.get(
        "/alerts/history",
        headers={"X-Tenant-ID": "alpha", "Authorization": "Bearer master-token"},
    )
    titles = [a["title"] for a in r.json()["anomalies"]]
    assert "A only" in titles
    assert "B only" not in titles


def test_04_alerts_history_beta_no_alpha(isolated_client):
    r = isolated_client.get(
        "/alerts/history",
        headers={"X-Tenant-ID": "beta", "Authorization": "Bearer master-token"},
    )
    titles = [a["title"] for a in r.json()["anomalies"]]
    assert "B only" in titles
    assert "A only" not in titles


def test_05_metrics_usage_separate(isolated_client):
    isolated_client.post(
        "/ask",
        json={"question": "q"},
        headers={"X-Tenant-ID": "alpha", "Authorization": "Bearer master-token"},
    )
    r = isolated_client.get(
        "/metrics/usage",
        headers={"X-Tenant-ID": "beta", "Authorization": "Bearer master-token"},
    )
    assert r.json()["tenant_id"] == "beta"


def test_09_api_token_required_when_set(isolated_client):
    r = isolated_client.get("/metrics/usage")
    assert r.status_code == 401


def test_10_report_daily_tenant_scope(isolated_client):
    r = isolated_client.get(
        "/report/daily",
        headers={"X-Tenant-ID": "alpha", "Authorization": "Bearer master-token"},
    )
    assert r.json()["tenant_id"] == "alpha"
