"""Auth endpoints: register, login, me.

Mounted at ``/api/auth`` by ``app.main``. Self-registration is open for
``promoter`` (an SME signing itself up is the primary path); ``auditor`` and
``banker`` — roles with real authority over certification and role-tagged
uploads — require a matching invite code (see ``Settings.banker_invite_code``
/ ``auditor_invite_code``). A blank invite code disables that role's
registration entirely.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import store as auth_store
from app.auth.dependencies import get_current_user
from app.auth.models import (
    LOGIN_ROLES,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    User,
    UserPublic,
)
from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
from app.db import get_session
from app.schema.models import Role

router = APIRouter()


def _check_invite(role: Role, invite_code: str | None) -> None:
    if role == Role.BANKER:
        required = settings.banker_invite_code
        label = "merchant banker"
    elif role == Role.AUDITOR:
        required = settings.auditor_invite_code
        label = "auditor"
    else:
        return  # promoter: open self-registration
    if not required or invite_code != required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"registering as {label} requires a valid invite code",
        )


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    if req.role not in LOGIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role must be one of {[r.value for r in LOGIN_ROLES]}",
        )
    _check_invite(req.role, req.invite_code)

    if await auth_store.get_by_email(session, req.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an account with this email already exists",
        )
    salt, pw_hash = hash_password(req.password)
    try:
        user = await auth_store.create(
            session,
            User(
                email=req.email,
                name=req.name,
                role=req.role,
                password_hash=pw_hash,
                password_salt=salt,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an account with this email already exists",
        ) from exc
    token = create_access_token(user_id=user.user_id, role=user.role.value, email=user.email)
    return TokenResponse(access_token=token, user=UserPublic.from_user(user))


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    user = await auth_store.get_by_email(session, req.email)
    if (
        user is None
        or user.disabled
        or not verify_password(req.password, user.password_salt, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )
    token = create_access_token(user_id=user.user_id, role=user.role.value, email=user.email)
    return TokenResponse(access_token=token, user=UserPublic.from_user(user))


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.from_user(current_user)
