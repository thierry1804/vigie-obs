import pytest

from agent.services.taxonomy import propose_taxonomy


@pytest.mark.asyncio
async def test_propose_taxonomy_writes_proposed_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    result = await propose_taxonomy("acme", days=3)

    assert result["tenant_id"] == "acme"
    assert (tmp_path / "acme.proposed.yaml").exists()
    assert result["taxonomy"]["events"][0]["name"] == "order_created"


@pytest.mark.asyncio
async def test_propose_taxonomy_falls_back_on_unparseable_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None):
        return "texte libre : ceci n'est pas du YAML valide : [}"

    monkeypatch.setattr(taxonomy_module, "run_agent", fake_run_agent)

    result = await propose_taxonomy("acme", days=7)

    assert result["taxonomy"]["events"] == []
    assert "raw" in result["taxonomy"]


@pytest.mark.asyncio
async def test_propose_taxonomy_falls_back_on_budget_exhausted_string(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None):
        return "Budget LLM épuisé (12000/10000 tokens)."

    monkeypatch.setattr(taxonomy_module, "run_agent", fake_run_agent)

    result = await propose_taxonomy("acme", days=7)

    assert result["taxonomy"] == {"events": [], "raw": "Budget LLM épuisé (12000/10000 tokens)."}


@pytest.mark.asyncio
async def test_propose_taxonomy_falls_back_on_harness_error_string(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.taxonomy as taxonomy_module

    monkeypatch.setattr(taxonomy_module, "TAXONOMY_DIR", tmp_path)

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", system_prompt=None):
        return "Erreur harness agentique : Connection refused"

    monkeypatch.setattr(taxonomy_module, "run_agent", fake_run_agent)

    result = await propose_taxonomy("acme", days=7)

    assert result["taxonomy"] == {"events": [], "raw": "Erreur harness agentique : Connection refused"}
