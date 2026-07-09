import pytest

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.mcp.auth import VigieTokenVerifier


@pytest.mark.asyncio
async def test_verify_token_returns_access_token_for_known_tenant():
    with get_session() as session:
        session.add(Tenant(id="acme", name="Acme", mcp_token="tok-acme"))
        session.commit()

    verifier = VigieTokenVerifier()
    access_token = await verifier.verify_token("tok-acme")

    assert access_token is not None
    assert access_token.client_id == "acme"
    assert access_token.subject == "acme"
    assert access_token.claims == {"tenant_id": "acme"}
    assert access_token.scopes == ["mcp"]


@pytest.mark.asyncio
async def test_verify_token_returns_none_for_unknown_token():
    verifier = VigieTokenVerifier()
    access_token = await verifier.verify_token("does-not-exist")
    assert access_token is None
