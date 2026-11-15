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
    report_date: date = Query(alias="date"),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> DailyReport:
    rollups = await load_rollups(
        session_factory,
        report_date - timedelta(days=1),
        report_date,
        settings.normalized_paid_statuses,
    )
    current = metrics_for(rollups, report_date)
    previous = metrics_for(rollups, report_date - timedelta(days=1))
    comparisons: dict[str, MetricComparison] = {}
    for metric in ("total_orders", "paid_orders", "paid_amount"):
        current_value = current[metric]
        previous_value = previous[metric]
        comparisons[metric] = MetricComparison(
            current=current_value,
            previous=previous_value,
            absolute_change=current_value - previous_value,
            percent_change=percent_change(current_value, previous_value),
        )
    return DailyReport(
        date=report_date,
        total_orders=int(current["total_orders"]),
        paid_orders=int(current["paid_orders"]),
        paid_amount=Decimal(current["paid_amount"]),
        status_counts=current["status_counts"],
        comparison=comparisons,
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
