"""Middleware tenant scoping et auth."""

import os

from fastapi import Header, HTTPException, Request

from agent.config import DEFAULT_TENANT_ID
from agent.db.models import Tenant
from agent.db.session import get_session


def get_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    authorization: str | None = Header(None),
) -> str:
    api_token = os.environ.get("VIGIE_API_TOKEN", "")
    if api_token:
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        if token != api_token:
            raise HTTPException(status_code=401, detail="Token API invalide")

    if x_tenant_id:
        return x_tenant_id

    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[7:]
        with get_session() as session:
            tenant = session.query(Tenant).filter(Tenant.api_token == bearer).first()
            if tenant:
                request.state.tenant_id = tenant.id
                return tenant.id

    return DEFAULT_TENANT_ID
