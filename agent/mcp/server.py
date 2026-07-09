"""Serveur MCP externe conforme au protocole (SDK mcp, transport Streamable HTTP)."""

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from agent.mcp.auth import VigieTokenVerifier
from agent.mcp.tools import register_tools

# Non utilisée par aucun code tant qu'aucun auth_server_provider n'est configuré (VIGIE
# ne fait pas office d'autorité OAuth) — requise uniquement par la validation Pydantic
# d'AuthSettings.issuer_url (champ obligatoire, sans défaut).
_ISSUER_URL = "http://vigie.local/"


def build_mcp_server() -> FastMCP:
    server = FastMCP(
        name="vigie",
        token_verifier=VigieTokenVerifier(),
        auth=AuthSettings(issuer_url=_ISSUER_URL, resource_server_url=None),
        streamable_http_path="/",
    )
    register_tools(server)
    return server


mcp_server = build_mcp_server()
mcp_app: Starlette = mcp_server.streamable_http_app()
