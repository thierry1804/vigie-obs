"""Vérification des tokens MCP externes — TokenVerifier custom pour le SDK mcp."""

from mcp.server.auth.provider import AccessToken, TokenVerifier

from agent.db.models import Tenant
from agent.db.session import get_session


class VigieTokenVerifier(TokenVerifier):
    """Vérifie un token MCP contre Tenant.mcp_token — même logique qu'avant l'API SDK."""

    async def verify_token(self, token: str) -> AccessToken | None:
        with get_session() as session:
            tenant = session.query(Tenant).filter(Tenant.mcp_token == token).first()
        if not tenant:
            return None
        return AccessToken(
            token=token,
            client_id=tenant.id,
            scopes=["mcp"],
            subject=tenant.id,
            claims={"tenant_id": tenant.id},
        )
