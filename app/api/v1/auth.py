"""POST /auth/register, /auth/login, /auth/refresh, /auth/logout — GET /auth/me."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.deps import CurrentUserDep, SessionDep, SettingsDep
from app.api.rate_limit import limiter
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
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 -- tipo de token OAuth2, no un secreto


class UserResponse(BaseModel):
    """Nunca lleva `password_hash`: el campo ni siquiera existe en este modelo,
    así que no hay forma de que se filtre por descuido al serializar.
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
async def me(user: CurrentUserDep) -> UserResponse:
    return UserResponse(
        id=user.id, email=user.email, status=user.status, created_at=user.created_at
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, user: CurrentUserDep, session: SessionDep) -> None:
    await auth_service.logout(session, user_id=user.id, raw_refresh_token=body.refresh_token)
