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
    session_factory: async_sessionmaker[AsyncSession],  # 异步会话工厂，用于创建数据库会话
    start_date: date,  # 查询开始日期
    end_date: date,  # 查询结束日期
    paid_statuses: tuple[str, ...],  # 已支付状态的元组
    keyword: str | None = None,  # 可选的关键词过滤条件
    status: str | None = None,  # 可选的状态过滤条件
) -> Rollups:  # 返回Rollups类型的聚合数据
    # 将日期时间转换为当天的开始时间
    start_at = datetime.combine(start_date, time.min)
    # 将结束日期转换为下一天的开始时间，用于范围查询
    end_at = datetime.combine(end_date + timedelta(days=1), time.min)
    # 初始化查询条件列表
    conditions = [Order.created_at >= start_at, Order.created_at < end_at]
    # 如果提供了关键词，添加到查询条件中
    if keyword:
        conditions.append(Order.keyword.contains(keyword, autoescape=True))
    # 如果提供了状态，添加到查询条件中（不区分大小写）
    if status:
        conditions.append(func.lower(Order.status) == status.lower())

    # 创建条件表达式：判断订单状态是否为已支付状态
    paid_order_count = case(
        (func.lower(Order.status).in_(paid_statuses), 1),
        else_=0,
    )
    # 创建条件表达式：计算已支付金额
    paid_amount = case(
        (func.lower(Order.status).in_(paid_statuses), Order.amount),
        else_=Decimal("0.00"),
    )
    # 构建SQL查询语句
    statement = (
        select(
            func.date(Order.created_at).label("order_date"),  # 订单日期
            Order.status.label("order_status"),  # 订单状态
            func.count(Order.id).label("total_orders"),  # 总订单数
            func.sum(paid_order_count).label("paid_orders"),  # 已支付订单数
            func.sum(paid_amount).label("paid_amount"),  # 已支付金额
        )
        .where(*conditions)  # 应用查询条件
        .group_by(func.date(Order.created_at), Order.status)  # 按日期和状态分组
        .order_by(func.date(Order.created_at), Order.status)  # 按日期和状态排序
    )

    # 执行查询并获取结果
    async with session_factory() as session:
        rows = (await session.execute(statement)).all()

    # 初始化结果字典，使用defaultdict和empty_metrics作为默认值
    result: Rollups = defaultdict(empty_metrics)
    # 处理查询结果
    for row in rows:
        order_date = _as_date(row.order_date)  # 将日期转换为date对象
        metric = result[order_date]
        status_counts = metric["status_counts"]
        assert isinstance(status_counts, dict)  # 断言status_counts是字典类型
        # 更新统计指标
        count = int(row.total_orders)
        status_name = str(row.order_status)
        metric["total_orders"] = int(metric["total_orders"]) + count
        metric["paid_orders"] = int(metric["paid_orders"]) + int(row.paid_orders or 0)
        metric["paid_amount"] = Decimal(metric["paid_amount"]) + Decimal(
            row.paid_amount or 0
        )
        # 更新状态计数
        status_counts[status_name] = status_counts.get(status_name, 0) + count

    return dict(result)  # 返回结果字典


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
