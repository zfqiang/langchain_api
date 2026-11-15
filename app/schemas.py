from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


Username = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.-]+$",
    ),
]
Password = Annotated[str, StringConstraints(min_length=12, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CreateUserRequest(BaseModel):
    username: Username
    password: Password
    role: Role


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime


class MetricComparison(BaseModel):
    current: int | Decimal
    previous: int | Decimal
    absolute_change: int | Decimal
    percent_change: Decimal | None


class DailyReport(BaseModel):
    date: date
    total_orders: int
    paid_orders: int
    paid_amount: Decimal
    status_counts: dict[str, int]
    comparison: dict[str, MetricComparison]


class KeywordDailyRow(BaseModel):
    date: date
    total_orders: int
    paid_orders: int
    paid_amount: Decimal
    previous_day_orders: int
    order_change: int
    order_change_percent: Decimal | None


class KeywordReport(BaseModel):
    keyword: str
    start_date: date
    end_date: date
    total_orders: int
    paid_orders: int
    paid_amount: Decimal
    daily: list[KeywordDailyRow]


class OrderRangeReport(BaseModel):
    start_date: date
    end_date: date
    total_orders: int
    paid_orders: int
    paid_amount: Decimal
    status_counts: dict[str, int]
    daily: list[KeywordDailyRow]


class AssistantQuery(BaseModel):
    query: Annotated[str, Field(min_length=3, max_length=2000)]


class AssistantResponse(BaseModel):
    answer: str
