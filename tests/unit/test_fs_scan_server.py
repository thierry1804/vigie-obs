import pytest

from agent.tools.fs_scan_server import build_discovery_tools
from discovery.scanner import DiscoveryReport, LogSource


def _report_with_source(tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("ligne 1\nligne 2\nligne 3\n", encoding="utf-8")
    source = LogSource(path=str(tmp_path), glob=str(tmp_path / "*.log"))
    return DiscoveryReport(target=str(tmp_path), log_sources=[source])


def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.asyncio
async def test_sample_lines_tool_resamples_existing_source(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "sample_lines")

    result = await tool.handler({"source_index": 0, "max_lines": 2})

    text = result["content"][0]["text"]
    assert "ligne 1" in text
    assert report.log_sources[0].sample_lines == ["ligne 1", "ligne 2"]


@pytest.mark.asyncio
async def test_sample_lines_tool_rejects_out_of_range_index(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "sample_lines")

    result = await tool.handler({"source_index": 5})

    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_set_framework_hint_tool_updates_report(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "set_framework_hint")

    result = await tool.handler({"source_index": 0, "framework": "laravel"})

    assert report.log_sources[0].framework_hint == "laravel"
    assert "laravel" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_set_framework_hint_tool_rejects_out_of_range_index(tmp_path):
    report = _report_with_source(tmp_path)
    tool = _tool_by_name(build_discovery_tools(report), "set_framework_hint")

    result = await tool.handler({"source_index": 5, "framework": "laravel"})

    assert result["is_error"] is True


def test_build_discovery_tools_returns_two_tools(tmp_path):
    report = _report_with_source(tmp_path)
    tools = build_discovery_tools(report)
    assert {t.name for t in tools} == {"sample_lines", "set_framework_hint"}
