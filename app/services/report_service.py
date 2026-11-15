import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import case, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.db.models import Order

logger = logging.getLogger(__name__)
MetricSet = dict[str, int | Decimal | dict[str, int]]
Rollups = dict[date, MetricSet]
T = TypeVar("T")


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def empty_metrics() -> MetricSet:
    return {
        "total_orders": 0,
        "paid_orders": 0,
        "paid_amount": Decimal("0.00"),
        "status_counts": {},
    }


def percent_change(current: int | Decimal, previous: int | Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100).quantize(
        Decimal("0.01")
    )


@retry(
    retry=retry_if_exception_type(OperationalError),
    wait=wait_exponential_jitter(initial=0.2, max=2),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def load_rollups(
    session_factory: async_sessionmaker[AsyncSession],
    start_date: date,
    end_date: date,
    paid_statuses: tuple[str, ...],
    keyword: str | None = None,
    status: str | None = None,
) -> Rollups:
    start_at = datetime.combine(start_date, time.min)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min)
    conditions = [Order.created_at >= start_at, Order.created_at < end_at]
    if keyword:
        conditions.append(Order.keyword.contains(keyword, autoescape=True))
    if status:
        conditions.append(func.lower(Order.status) == status.lower())

    paid_order_count = case(
        (func.lower(Order.status).in_(paid_statuses), 1),
        else_=0,
    )
    paid_amount = case(
        (func.lower(Order.status).in_(paid_statuses), Order.amount),
        else_=Decimal("0.00"),
    )
    statement = (
        select(
            func.date(Order.created_at).label("order_date"),
            Order.status.label("order_status"),
            func.count(Order.id).label("total_orders"),
            func.sum(paid_order_count).label("paid_orders"),
            func.sum(paid_amount).label("paid_amount"),
        )
        .where(*conditions)
        .group_by(func.date(Order.created_at), Order.status)
        .order_by(func.date(Order.created_at), Order.status)
    )

    async with session_factory() as session:
        rows = (await session.execute(statement)).all()

    result: Rollups = defaultdict(empty_metrics)
    for row in rows:
        order_date = _as_date(row.order_date)
        metric = result[order_date]
        status_counts = metric["status_counts"]
        assert isinstance(status_counts, dict)
        count = int(row.total_orders)
        status_name = str(row.order_status)
        metric["total_orders"] = int(metric["total_orders"]) + count
        metric["paid_orders"] = int(metric["paid_orders"]) + int(row.paid_orders or 0)
        metric["paid_amount"] = Decimal(metric["paid_amount"]) + Decimal(
            row.paid_amount or 0
        )
        status_counts[status_name] = status_counts.get(status_name, 0) + count

    return dict(result)


def metrics_for(rollups: Rollups, day: date) -> MetricSet:
    return rollups.get(day, empty_metrics())


def daily_rows(
    rollups: Rollups,
    start_date: date,
    end_date: date,
) -> list[dict[str, date | int | Decimal | None]]:
    rows: list[dict[str, date | int | Decimal | None]] = []
    day = start_date
    while day <= end_date:
        current = metrics_for(rollups, day)
        previous = metrics_for(rollups, day - timedelta(days=1))
        current_orders = int(current["total_orders"])
        previous_orders = int(previous["total_orders"])
        rows.append(
            {
                "date": day,
                "total_orders": current_orders,
                "paid_orders": int(current["paid_orders"]),
                "paid_amount": Decimal(current["paid_amount"]),
                "previous_day_orders": previous_orders,
                "order_change": current_orders - previous_orders,
                "order_change_percent": percent_change(current_orders, previous_orders),
            }
        )
        day += timedelta(days=1)
    return rows
