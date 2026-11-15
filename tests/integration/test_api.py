import os
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.security import hash_password
from app.db.models import Base, Order, User


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        window: int,
    ) -> list[int]:
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        return [count, window]

    async def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_login_daily_report_permissions_and_rate_limit() -> None:
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "JWT_SECRET_KEY": "test-secret-key-that-is-at-least-32-characters",
        },
    ):
        from app.main import create_app

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://127.0.0.1:6379/0",
        jwt_secret_key="test-secret-key-that-is-at-least-32-characters",
        rate_limit_requests=1,
    )
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    username="analyst",
                    hashed_password=hash_password("safe-password-123"),
                    role="analyst",
                ),
                Order(
                    created_at=datetime(2026, 1, 1, 9),
                    status="pending",
                    amount=Decimal("3.00"),
                    keyword="coffee",
                ),
                Order(
                    created_at=datetime(2026, 1, 1, 10),
                    status="paid",
                    amount=Decimal("5.00"),
                    keyword="coffee",
                ),
                Order(
                    created_at=datetime(2026, 1, 2, 10),
                    status="paid",
                    amount=Decimal("12.50"),
                    keyword="coffee",
                ),
            ]
        )
        await session.commit()

    application = create_app(
        settings=settings,
        session_factory=session_factory,
        redis_client=FakeRedis(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            login = await client.post(
                "/api/v1/auth/token",
                data={"username": "analyst", "password": "safe-password-123"},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            report = await client.get(
                "/api/v1/reports/daily?date=2026-01-02",
                headers=headers,
            )
            assert report.status_code == 200
            assert report.headers["x-request-id"]
            assert report.json()["total_orders"] == 1
            assert report.json()["paid_amount"] == "12.50"
            assert report.json()["comparison"]["total_orders"]["absolute_change"] == -1

            limited = await client.get(
                "/api/v1/reports/daily?date=2026-01-02",
                headers=headers,
            )
            assert limited.status_code == 429
            assert limited.headers["retry-after"]
    finally:
        await engine.dispose()
