import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.routes.assistant import router as assistant_router
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.reports import router as reports_router
from app.core.config import Settings, get_settings
from app.core.dependencies import get_redis, get_session_factory
from app.db.session import create_database
from app.services.assistant_service import create_report_agent

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    owns_database = session_factory is None
    owns_redis = redis_client is None
    engine = None
    if session_factory is None:
        engine, session_factory = create_database(app_settings.database_url)
    if redis_client is None:
        redis_client = Redis.from_url(
            app_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            try:
                if owns_redis:
                    await redis_client.aclose()
            finally:
                if owns_database and engine is not None:
                    await engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.redis = redis_client
    app.state.agent = create_report_agent(app_settings, session_factory)
    app.include_router(auth_router)
    app.include_router(reports_router)
    app.include_router(assistant_router)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(
        session_factory: async_sessionmaker[AsyncSession] = Depends(
            get_session_factory
        ),
        redis: Redis = Depends(get_redis),
    ) -> Response:
        components: dict[str, str] = {"mysql": "ok", "redis": "ok"}
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("MySQL readiness check failed")
            components["mysql"] = "unavailable"
        try:
            await redis.ping()
        except RedisError:
            logger.exception("Redis readiness check failed")
            components["redis"] = "unavailable"
        if "unavailable" in components.values():
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "components": components},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "components": components},
        )

    return app


app = create_app()
