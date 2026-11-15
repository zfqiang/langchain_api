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
    settings: Settings | None = None,  # 应用配置参数，可选类型为Settings或None
    session_factory: async_sessionmaker[AsyncSession] | None = None,  # 异步会话工厂，可选类型为async_sessionmaker[AsyncSession]或None
    redis_client: Redis | None = None,  # Redis客户端，可选类型为Redis或None
) -> FastAPI:  # 返回类型为FastAPI应用实例
    # 初始化应用配置，如果未提供则使用默认配置
    app_settings = settings or get_settings()
    # 标记是否拥有数据库连接
    owns_database = session_factory is None
    # 标记是否拥有Redis连接
    owns_redis = redis_client is None
    engine = None  # 初始化数据库引擎为None
    # 如果未提供会话工厂，则创建数据库连接和会话工厂
    if session_factory is None:
        engine, session_factory = create_database(app_settings.database_url)
    # 如果未提供Redis客户端，则创建Redis连接
    if redis_client is None:
        redis_client = Redis.from_url(
            app_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )

    # 定义应用生命周期管理器
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield  # 应用运行时
        finally:
            try:
                # 如果拥有Redis连接，则关闭连接
                if owns_redis:
                    await redis_client.aclose()
            finally:
                # 如果拥有数据库连接，则关闭引擎
                if owns_database and engine is not None:
                    await engine.dispose()

    # 创建FastAPI应用实例
    app = FastAPI(
        title=app_settings.app_name,  # 应用名称
        version="1.0.0",  # 应用版本
        lifespan=lifespan,  # 生命周期管理器
    )
    # 将配置、会话工厂和Redis客户端存储到应用状态中
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.redis = redis_client
    # 创建报告代理并存储到应用状态中
    app.state.agent = create_report_agent(app_settings, session_factory)

    # 包含路由
    app.include_router(auth_router)  # 认证路由
    app.include_router(reports_router)  # 报告路由
    app.include_router(assistant_router)  # 助手路由

    # 定义HTTP中间件，用于添加请求ID
    @app.middleware("http")
    async def add_request_id(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())  # 从请求头获取或生成请求ID
        response = await call_next(request)  # 处理请求
        response.headers["X-Request-ID"] = request_id  # 在响应头中添加请求ID
        return response

    # 定义存活状态检查端点
    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}  # 返回存活状态

    # 定义就绪状态检查端点
    @app.get("/health/ready", tags=["health"])
    async def readiness(
        session_factory: async_sessionmaker[AsyncSession] = Depends(
            get_session_factory
        ),
        redis: Redis = Depends(get_redis),
    ) -> Response:
        # 初始化组件状态字典
        components: dict[str, str] = {"mysql": "ok", "redis": "ok"}
        # 检查数据库连接
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("MySQL readiness check failed")
            components["mysql"] = "unavailable"
        # 检查Redis连接
        try:
            await redis.ping()
        except RedisError:
            logger.exception("Redis readiness check failed")
            components["redis"] = "unavailable"
        # 如果有组件不可用，返回503状态码
        if "unavailable" in components.values():
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "components": components},
            )
        # 所有组件正常，返回200状态码
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "components": components},
        )

    return app  # 返回创建的FastAPI应用实例


app = create_app()
