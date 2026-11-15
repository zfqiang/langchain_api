from datetime import date
from decimal import Decimal
from typing import Annotated

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.tools import ToolException
from langchain_openai import ChatOpenAI
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.report_service import load_rollups

SYSTEM_PROMPT = """你是订单日结报表助手。回答前必须调用 query_order_metrics 工具，
只能根据工具返回的聚合数据作答。绝不编写、展示或执行 SQL，不要推断工具未返回的数据。
工具仅支持最多 90 天的日期范围；日期不明确时先询问用户。
金额和订单状态以工具结果为准。"""


def create_report_agent(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    if settings.llm_api_key is None:
        return None

    @tool
    async def query_order_metrics(
        start_date: Annotated[date, Field(description="统计开始日期，YYYY-MM-DD")],
        end_date: Annotated[date, Field(description="统计结束日期，YYYY-MM-DD")],
        keyword: Annotated[
            str | None, Field(description="可选关键词，按包含关系匹配")
        ] = None,
        order_status: Annotated[str | None, Field(description="可选订单状态")] = None,
    ) -> dict[str, object]:
        """查询指定日期范围内的订单数、支付金额、状态分布及逐日变化。"""
        if end_date < start_date:
            raise ToolException("结束日期必须不早于开始日期。")
        if (end_date - start_date).days + 1 > settings.report_max_range_days:
            raise ToolException(
                f"日期范围不能超过 {settings.report_max_range_days} 天。"
            )
        rollups = await load_rollups(
            session_factory,
            start_date,
            end_date,
            settings.normalized_paid_statuses,
            keyword=keyword,
            status=order_status,
        )
        rows = []
        day = start_date
        while day <= end_date:
            current = rollups.get(
                day,
                {
                    "total_orders": 0,
                    "paid_orders": 0,
                    "paid_amount": Decimal("0.00"),
                    "status_counts": {},
                },
            )
            rows.append(
                {
                    "date": day.isoformat(),
                    "total_orders": int(current["total_orders"]),
                    "paid_orders": int(current["paid_orders"]),
                    "paid_amount": str(current["paid_amount"]),
                    "status_counts": current["status_counts"],
                }
            )
            day = date.fromordinal(day.toordinal() + 1)
        return {
            "keyword": keyword,
            "status_filter": order_status,
            "daily": rows,
        }

    model = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )
    return create_agent(
        model=model,
        tools=[query_order_metrics],
        system_prompt=SYSTEM_PROMPT,
    )
