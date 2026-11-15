from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Order
from app.services.report_service import load_rollups, percent_change


@pytest.mark.asyncio
async def test_daily_rollups_and_keyword_filter() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            session.add_all(
                [
                    Order(
                        created_at=datetime(2026, 1, 1, 9),
                        status="paid",
                        amount=Decimal("10.50"),
                        keyword="coffee",
                    ),
                    Order(
                        created_at=datetime(2026, 1, 1, 11),
                        status="pending",
                        amount=Decimal("3.00"),
                        keyword="coffee beans",
                    ),
                    Order(
                        created_at=datetime(2026, 1, 2, 8),
                        status="paid",
                        amount=Decimal("7.25"),
                        keyword="tea",
                    ),
                ]
            )
            await session.commit()

        rollups = await load_rollups(
            session_factory,
            date(2026, 1, 1),
            date(2026, 1, 2),
            ("paid", "completed"),
            keyword="coffee",
        )

        assert rollups[date(2026, 1, 1)] == {
            "total_orders": 2,
            "paid_orders": 1,
            "paid_amount": Decimal("10.50"),
            "status_counts": {"paid": 1, "pending": 1},
        }
        assert date(2026, 1, 2) not in rollups
    finally:
        await engine.dispose()


def test_percent_change_handles_zero_and_negative_change() -> None:
    assert percent_change(3, 0) is None
    assert percent_change(3, 4) == Decimal("-25.00")
