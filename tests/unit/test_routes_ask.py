from fastapi.testclient import TestClient

from agent.main import app


def test_ask_route_calls_run_agent_with_ask_preset(monkeypatch):
    import agent.routes.ask as ask_module

    captured = {}

    async def fake_run_agent(
        preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None, **kwargs
    ):
        captured["preset"] = preset
        captured["tenant_id"] = tenant_id
        captured["endpoint"] = endpoint
        return "réponse factice"

    monkeypatch.setattr(ask_module, "run_agent", fake_run_agent)

    with TestClient(app) as client:
        r = client.post(
            "/ask", json={"question": "pourquoi ça plante ?"}, headers={"X-Tenant-ID": "acme"}
        )

    assert r.status_code == 200
    assert r.json() == {"answer": "réponse factice", "tenant_id": "acme"}
    assert captured == {"preset": "ask", "tenant_id": "acme", "endpoint": "ask"}
