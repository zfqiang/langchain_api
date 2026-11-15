from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.dependencies import (
    get_session_factory,
    get_settings,
    rate_limit_authenticated,
    require_roles,
)
from app.schemas import (
    DailyReport,
    KeywordReport,
    MetricComparison,
    OrderRangeReport,
    Role,
)
from app.services.report_service import (
    daily_rows,
    load_rollups,
    metrics_for,
    percent_change,
)

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(rate_limit_authenticated)],
)
REPORT_ROLES = (Role.ADMIN, Role.ANALYST, Role.VIEWER)


def validate_range(start_date: date, end_date: date, settings: Settings) -> None:
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )
    if (end_date - start_date).days + 1 > settings.report_max_range_days:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Date range cannot exceed {settings.report_max_range_days} days",
        )


@router.get(
    "/daily",
    response_model=DailyReport,
    dependencies=[Depends(require_roles(*REPORT_ROLES))],
)
async def get_daily_report(
    report_date: date = Query(alias="date"),  # 报告日期，通过查询参数date获取
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # 异步会话工厂，依赖注入获取
    settings: Settings = Depends(get_settings),  # 设置对象，依赖注入获取
) -> DailyReport:  # 返回类型为DailyReport
    # 加载汇总数据，日期范围为前一日到报告日期
    rollups = await load_rollups(
        session_factory,
        report_date - timedelta(days=1),  # 开始日期：报告日期的前一天
        report_date,  # 结束日期：报告日期
        settings.normalized_paid_statuses,  # 已支付状态列表
    )

    # 获取报告日期和前一日的指标数据
    current = metrics_for(rollups, report_date)  # 当前日期的指标
    previous = metrics_for(rollups, report_date - timedelta(days=1))  # 前一日的指标
    # 创建指标比较字典，存储当前值、前值、绝对变化和百分比变化
    comparisons: dict[str, MetricComparison] = {}
    for metric in ("total_orders", "paid_orders", "paid_amount"):  # 遍历三个关键指标
        current_value = current[metric]  # 获取当前值
        previous_value = previous[metric]  # 获取前值
        comparisons[metric] = MetricComparison(  # 创建指标比较对象
            current=current_value,
            previous=previous_value,
            absolute_change=current_value - previous_value,  # 计算绝对变化
            percent_change=percent_change(current_value, previous_value),  # 计算百分比变化
        )
    # 返回日报对象，包含日期、各项指标和比较数据
    return DailyReport(
        date=report_date,
        total_orders=int(current["total_orders"]),  # 总订单数，转为整数
        paid_orders=int(current["paid_orders"]),  # 已支付订单数，转为整数
        paid_amount=Decimal(current["paid_amount"]),  # 已支付金额，转为Decimal
        status_counts=current["status_counts"],  # 订单状态计数
        comparison=comparisons,  # 指标比较数据
    )


@router.get(
    "/keyword",
    response_model=KeywordReport,
    dependencies=[Depends(require_roles(*REPORT_ROLES))],
)
async def get_keyword_report(
    keyword: str = Query(min_length=1, max_length=100),
    start_date: date = Query(),
    end_date: date = Query(),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> KeywordReport:
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="keyword cannot be blank",
        )
    validate_range(start_date, end_date, settings)
    rollups = await load_rollups(
        session_factory,
        start_date - timedelta(days=1),
        end_date,
        settings.normalized_paid_statuses,
        keyword=keyword,
    )
    selected_days = [day for day in rollups if start_date <= day <= end_date]
    return KeywordReport(
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        total_orders=sum(
            int(metrics_for(rollups, day)["total_orders"]) for day in selected_days
        ),
        paid_orders=sum(
            int(metrics_for(rollups, day)["paid_orders"]) for day in selected_days
        ),
        paid_amount=sum(
            (
                Decimal(metrics_for(rollups, day)["paid_amount"])
                for day in selected_days
            ),
            Decimal("0.00"),
        ),
        daily=daily_rows(rollups, start_date, end_date),
    )


@router.get(
    "/orders",
    response_model=OrderRangeReport,
    dependencies=[Depends(require_roles(*REPORT_ROLES))],
)
async def get_order_report(
    start_date: date = Query(),
    end_date: date = Query(),
    order_status: str | None = Query(
        default=None,
        alias="status",
        min_length=1,
        max_length=32,
    ),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> OrderRangeReport:
    validate_range(start_date, end_date, settings)
    if order_status is not None:
        order_status = order_status.strip()
        if not order_status:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status cannot be blank",
            )
    rollups = await load_rollups(
        session_factory,
        start_date - timedelta(days=1),
        end_date,
        settings.normalized_paid_statuses,
        status=order_status,
    )
    selected_days = [day for day in rollups if start_date <= day <= end_date]
    status_counts: dict[str, int] = {}
    for day in selected_days:
        counts = metrics_for(rollups, day)["status_counts"]
        for name, count in counts.items():
            status_counts[name] = status_counts.get(name, 0) + count
    return OrderRangeReport(
        start_date=start_date,
        end_date=end_date,
        total_orders=sum(
            int(metrics_for(rollups, day)["total_orders"]) for day in selected_days
        ),
        paid_orders=sum(
            int(metrics_for(rollups, day)["paid_orders"]) for day in selected_days
        ),
        paid_amount=sum(
            (
                Decimal(metrics_for(rollups, day)["paid_amount"])
                for day in selected_days
            ),
            Decimal("0.00"),
        ),
        status_counts=status_counts,
        daily=daily_rows(rollups, start_date, end_date),
    )
