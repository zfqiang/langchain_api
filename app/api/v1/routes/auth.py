import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.dependencies import (
    get_db,
    get_settings,
    rate_limit_authenticated,
    rate_limit_login,
    require_roles,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.schemas import (
    CreateUserRequest,
    Role,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["authentication"])


@router.post(
    "/auth/token",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_login)],
)
async def issue_token(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = await session.scalar(select(User).where(User.username == form.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(form.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(settings, user.id),
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.post(
    "/admin/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_authenticated)],
)
async def create_user(
    data: CreateUserRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles(Role.ADMIN)),
) -> User:
    user = User(
        username=data.username,
        hashed_password=hash_password(data.password),
        role=data.role.value,
    )
    session.add(user)
    try:
        await session.commit()
        await session.refresh(user)
    except IntegrityError as exc:
        await session.rollback()
        logger.info("User creation rejected because the username already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        ) from exc
    return user
