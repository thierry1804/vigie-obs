import pytest

from agent.services.triage import triage_alert


@pytest.mark.asyncio
async def test_triage_alert_mock_returns_anomaly(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    is_anomaly, reason = await triage_alert("acme", "sig1", "erreur 500 répétée")
    assert is_anomaly is True
    assert reason == "anomalie plausible (mock)"


@pytest.mark.asyncio
async def test_triage_alert_uses_cache_on_second_call(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    await triage_alert("acme", "sig-cache", "contexte identique")
    is_anomaly, source = await triage_alert("acme", "sig-cache", "contexte identique")
    assert source == "cache"
