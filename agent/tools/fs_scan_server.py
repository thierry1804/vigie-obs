"""Serveur MCP in-process (outils discovery) — bornés à un DiscoveryReport déjà scanné."""

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from discovery import scanner
from discovery.scanner import DiscoveryReport


def build_discovery_tools(report: DiscoveryReport) -> list[SdkMcpTool[Any]]:
    """Construit les 2 outils discovery liés à un DiscoveryReport précis."""

    @tool(
        "sample_lines",
        "Ré-échantillonne les lignes d'une source de logs déjà découverte.",
        {
            "type": "object",
            "properties": {
                "source_index": {"type": "integer"},
                "max_lines": {"type": "integer"},
            },
            "required": ["source_index"],
        },
    )
    async def sample_lines_tool(args: dict[str, Any]) -> dict[str, Any]:
        index = args["source_index"]
        if index < 0 or index >= len(report.log_sources):
            return {
                "content": [{"type": "text", "text": f"source_index invalide : {index}"}],
                "is_error": True,
            }
        source = report.log_sources[index]
        scanner.sample_lines(source, max_lines=args.get("max_lines", 20))
        text = "\n".join(source.sample_lines) or "Aucune ligne échantillonnée."
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "set_framework_hint",
        "Enregistre la conclusion de classification (framework) pour une source de logs.",
        {
            "type": "object",
            "properties": {
                "source_index": {"type": "integer"},
                "framework": {"type": "string"},
            },
            "required": ["source_index", "framework"],
        },
    )
    async def set_framework_hint_tool(args: dict[str, Any]) -> dict[str, Any]:
        index = args["source_index"]
        if index < 0 or index >= len(report.log_sources):
            return {
                "content": [{"type": "text", "text": f"source_index invalide : {index}"}],
                "is_error": True,
            }
        framework = args["framework"]
        report.log_sources[index].framework_hint = framework
        text = f"framework_hint mis à jour pour la source {index} : {framework}"
        return {"content": [{"type": "text", "text": text}]}

    return [sample_lines_tool, set_framework_hint_tool]


def build_fs_scan_mcp_server(report: DiscoveryReport) -> McpSdkServerConfig:
    """Serveur MCP in-process prêt pour ClaudeAgentOptions.mcp_servers."""
    return create_sdk_mcp_server("vigie-fs", tools=build_discovery_tools(report))
