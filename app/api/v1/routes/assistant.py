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
    """调用自然语言助手 Agent 处理用户查询并返回回答。

    Args:
        request: 包含用户查询文本的请求体,查询长度限制为 3–2000 字符。
        http_request: FastAPI 请求对象,用于访问应用级别的 Agent 实例。

    Returns:
        AssistantResponse: 包含助手回答文本的响应体。

    Raises:
        HTTPException: 当 Agent 未配置时返回 503；当语言模型请求失败、
            未返回响应或返回空内容时返回 502。
    """
    # 从应用状态获取 Agent 实例,若未配置则拒绝请求
    agent = http_request.app.state.agent
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Natural-language assistant is not configured",
        )

    # 以用户查询为输入调用 Agent 进行异步推理,递归限制为 8 防止无限循环
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

    # 提取最后一条消息的文本内容,兼容纯字符串和多模态块列表两种响应格式
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
