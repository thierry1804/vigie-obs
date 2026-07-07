from fastapi import APIRouter

from agent.config import APP_VERSION

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": "vigie-agent", "version": APP_VERSION}
