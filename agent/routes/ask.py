from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.middleware.tenant import get_tenant_id
from agent.services.agent_loop import agent_loop

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask(req: AskRequest, tenant_id: str = Depends(get_tenant_id)):
    answer = await agent_loop(req.question, tenant_id=tenant_id, endpoint="ask")
    return {"answer": answer, "tenant_id": tenant_id}
