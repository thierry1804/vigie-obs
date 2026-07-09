import pytest

from agent.services.discovery import infer_formats
from discovery.scanner import DiscoveryReport, LogSource


@pytest.mark.asyncio
async def test_infer_formats_applies_agent_conclusion(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.discovery as discovery_module

    source = LogSource(path=str(tmp_path), glob=str(tmp_path / "*.log"), sample_lines=["{}"])
    report = DiscoveryReport(target=str(tmp_path), log_sources=[source])

    async def fake_run_agent(preset, user_message, tenant_id="default", endpoint="ask", **kwargs):
        kwargs["report"].log_sources[0].framework_hint = "laravel"
        return "Classification terminée."

    monkeypatch.setattr(discovery_module, "run_agent", fake_run_agent)

    result = await infer_formats(report, tenant_id="acme")

    assert result.log_sources[0].framework_hint == "laravel"


@pytest.mark.asyncio
async def test_run_discovery_skips_agent_when_no_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")
    import agent.services.discovery as discovery_module

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("run_agent must not be called when there are no log sources")

    monkeypatch.setattr(discovery_module, "run_agent", fail_if_called)

    result = await discovery_module.run_discovery(str(tmp_path), tenant_id="acme")

    assert result["report"]["log_sources"] == []
