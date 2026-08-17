"""POST /auth/register, /auth/login, /auth/refresh, /auth/logout — GET /auth/me."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.deps import CurrentUserDep, SessionDep, SettingsDep
from app.api.rate_limit import GLOBAL_RATE_LIMIT, limiter
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 -- OAuth2 token type, not a secret


class UserResponse(BaseModel):
    """Never carries `password_hash`: the field doesn't even exist on this
    model, so there's no way it leaks by accident when serializing.
    """

    id: UUID
    email: str
    status: str
    created_at: datetime


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request, body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    _, tokens = await auth_service.register_user(
        session, settings, email=body.email, password=body.password
    )
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request, body: LoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    _, tokens = await auth_service.login(
        session, settings, email=body.email, password=body.password
    )
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
async def refresh(
    request: Request, body: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    tokens = await auth_service.refresh_access_token(
        session, settings, raw_refresh_token=body.refresh_token
    )
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.get("/me", response_model=UserResponse)
@limiter.limit(GLOBAL_RATE_LIMIT)
async def me(request: Request, user: CurrentUserDep) -> UserResponse:
    return UserResponse(
        id=user.id, email=user.email, status=user.status, created_at=user.created_at
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(GLOBAL_RATE_LIMIT)
async def logout(
    request: Request, body: RefreshRequest, user: CurrentUserDep, session: SessionDep
) -> None:
    await auth_service.logout(session, user_id=user.id, raw_refresh_token=body.refresh_token)
