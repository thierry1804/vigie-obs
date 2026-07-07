from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.middleware.tenant import get_tenant_id
from agent.services.discovery import run_discovery

router = APIRouter(tags=["discover"])


class DiscoverRequest(BaseModel):
    target: str
    existing_config: str | None = None


@router.post("/discover")
async def discover(req: DiscoverRequest, tenant_id: str = Depends(get_tenant_id)):
    existing = Path(req.existing_config) if req.existing_config else None
    result = run_discovery(req.target, tenant_id=tenant_id, existing_config=existing)
    return result
