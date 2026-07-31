"""FastAPI dependencies enforcing authentication and role membership.

This is the server-side half of promoter/auditor/banker separation. The
frontend's role switch used to be the only thing filtering what a user could
see or do; now every mutating and read endpoint in ``app.main`` depends on
:func:`get_current_user` (valid token required) or :func:`require_roles`
(valid token AND role membership required) — a client cannot reach a
banker-only action by editing local state or crafting a request by hand.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import User
from app.auth.security import InvalidToken, decode_access_token
from app.auth.store import get_user_store
from app.schema.models import Role

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated — send an Authorization: Bearer <token> header",
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc
    user = get_user_store().get_by_id(payload["sub"])
    if user is None or user.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or disabled"
        )
    return user


def require_roles(*roles: Role) -> Callable[..., Awaitable[User]]:
    """Dependency factory: 401 if unauthenticated, 403 if the wrong role."""

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"this action requires role in {[r.value for r in roles]}, "
                    f"account role is {user.role.value}"
                ),
            )
        return user

    return _dependency
