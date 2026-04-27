"""FastAPI dependencies for API-key auth + CORS wiring instructions.

# WIRING (in app/main.py):
#
#     from fastapi.middleware.cors import CORSMiddleware
#     from app.security import admin_router
#     from app.config import get_settings
#
#     _settings = get_settings()
#     app.add_middleware(
#         CORSMiddleware,
#         allow_origins=_settings.allowed_origins,
#         allow_credentials=True,
#         allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
#         allow_headers=["Authorization", "Content-Type"],
#     )
#     app.include_router(admin_router)
#
# Then on protected routes:
#     from app.security import require_api_key, rate_limit_dependency
#     @router.get("/foo", dependencies=[Depends(require_api_key()), Depends(rate_limit_dependency)])
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status

from app.db import session_scope
from app.security import api_keys as ak

logger = logging.getLogger(__name__)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_api_key(scopes: list[str] | None = None) -> Callable:
    required = list(scopes or [])

    async def _dep(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> "ak.ApiKey":
        token = _extract_bearer(authorization)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        async with session_scope() as session:
            row = await ak.get_by_token(session, token)
            if row is None:
                logger.warning("api_key.auth_failed token=%s", ak._safe_token_repr(token))
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if row.revoked_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked"
                )
            if required:
                granted = set(row.scopes or [])
                if "*" not in granted and not set(required).issubset(granted):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing required scopes: {required}",
                    )
            await ak.touch_last_used(session, row.id)
            request.state.api_key = row
            return row

    return _dep


# Admin token guard — used by admin_router.
ADMIN_TOKEN_ENV = "ADMIN_TOKEN"


async def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    import os

    expected = os.environ.get(ADMIN_TOKEN_ENV)
    if not expected:
        # Fail closed: if the env var is missing, admin endpoints are disabled.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Admin disabled: {ADMIN_TOKEN_ENV} not set",
        )
    token = _extract_bearer(authorization) or ""
    if not ak.constant_time_eq(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
