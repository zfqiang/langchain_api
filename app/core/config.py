"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Order Analytics API"
    environment: str = "development"
    database_url: Annotated[str, Field(min_length=1)]
    redis_url: Annotated[str, Field(min_length=1)]
    jwt_secret_key: Annotated[SecretStr, Field(min_length=32)]
    jwt_expire_minutes: Annotated[int, Field(gt=0, le=1440)] = 60
    jwt_issuer: str = "order-analytics"
    jwt_audience: str = "order-analytics-api"
    paid_order_statuses: list[Annotated[str, Field(min_length=1, max_length=32)]] = [
        "paid",
        "completed",
    ]
    rate_limit_requests: Annotated[int, Field(gt=0)] = 60
    rate_limit_window_seconds: Annotated[int, Field(gt=0)] = 60
    report_max_range_days: Annotated[int, Field(gt=0, le=366)] = 90
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 30

    @field_validator("paid_order_statuses")
    @classmethod
    def normalize_paid_order_statuses(cls, statuses: list[str]) -> list[str]:
        normalized = [status.strip().lower() for status in statuses]
        if not normalized or any(not status for status in normalized):
            raise ValueError("paid_order_statuses must contain non-empty values")
        return normalized

    @property
    def normalized_paid_statuses(self) -> tuple[str, ...]:
        return tuple(self.paid_order_statuses)


@lru_cache
def get_settings() -> Settings:
    return Settings()
