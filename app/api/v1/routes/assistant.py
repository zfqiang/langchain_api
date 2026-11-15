import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from openai import OpenAIError

from app.core.dependencies import (
    rate_limit_authenticated,
    require_roles,
)
from app.schemas import AssistantQuery, AssistantResponse, Role

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["assistant"],
    dependencies=[
        Depends(rate_limit_authenticated),
        Depends(require_roles(Role.ADMIN, Role.ANALYST, Role.VIEWER)),
    ],
)


@router.post("/query", response_model=AssistantResponse)
async def query_assistant(
    request: AssistantQuery,
    http_request: Request,
) -> AssistantResponse:
    agent = http_request.app.state.agent
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Natural-language assistant is not configured",
        )
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": request.query}]},
            config={"recursion_limit": 8},
        )
    except OpenAIError as exc:
        logger.exception("Language model request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Language model request failed",
        ) from exc

    messages = result.get("messages", [])
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Language model returned no response",
        )
    content = messages[-1].content
    if isinstance(content, str):
        answer = content.strip()
    elif isinstance(content, list):
        answer = "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ).strip()
    else:
        answer = ""
    if not answer:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Language model returned an empty response",
        )
    return AssistantResponse(answer=answer)
