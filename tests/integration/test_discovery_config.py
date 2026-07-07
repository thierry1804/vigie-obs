from agent.services.discovery import run_discovery


def test_run_discovery_generates_config(tmp_path):
    target = tmp_path / "symfony"
    (target / "var" / "log").mkdir(parents=True)
    (target / "var" / "log" / "dev.log").write_text("test log\n", encoding="utf-8")
    result = run_discovery(str(target), tenant_id="default")
    assert "proposed_config" in result
    assert "tenant_id" in result["proposed_config"]
    assert "[sinks.loki]" in result["proposed_config"]
