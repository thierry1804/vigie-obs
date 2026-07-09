from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.harness.runner import run_agent
from agent.middleware.tenant import get_tenant_id

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask(req: AskRequest, tenant_id: str = Depends(get_tenant_id)):
    answer = await run_agent("ask", req.question, tenant_id=tenant_id, endpoint="ask")
    return {"answer": answer, "tenant_id": tenant_id}
