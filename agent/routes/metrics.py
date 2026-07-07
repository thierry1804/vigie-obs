from fastapi import APIRouter, Depends

from agent.middleware.tenant import get_tenant_id
from agent.services.tokens import get_usage_summary

router = APIRouter(tags=["metrics"])


@router.get("/metrics/usage")
async def metrics_usage(tenant_id: str = Depends(get_tenant_id)):
    return get_usage_summary(tenant_id)
