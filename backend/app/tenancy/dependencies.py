"""FastAPI dependencies for tenancy.

The api-key middleware is expected to set ``request.state.api_key`` with
attributes ``id``, ``org_id`` and ``role`` (the latter resolved from a
membership row at request time). These deps surface that context to
routers without each one repeating the boilerplate.
"""
from __future__ import annotations

import uuid
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from .models import Role
from .policy import ROLE_RANK


def current_org(request: Request) -> uuid.UUID:
    """Return the org_id bound to the request's api key, or 401."""
    api_key = getattr(request.state, "api_key", None)
    org_id = getattr(api_key, "org_id", None) if api_key is not None else None
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="api key is not bound to an organization",
        )
    if isinstance(org_id, str):
        try:
            org_id = uuid.UUID(org_id)
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail="invalid org_id") from exc
    return org_id


def _current_role(request: Request) -> Optional[str]:
    api_key = getattr(request.state, "api_key", None)
    if api_key is None:
        return None
    role = getattr(api_key, "role", None)
    if role is None:
        return None
    return role.value if hasattr(role, "value") else str(role)


def require_role(role: str | Role) -> Callable:
    """Return a FastAPI dependency that requires at least ``role``."""
    needed = role.value if isinstance(role, Role) else str(role)
    if needed not in ROLE_RANK:
        raise ValueError(f"unknown role: {needed!r}")
    needed_rank = ROLE_RANK[needed]

    def _dep(request: Request, _org: uuid.UUID = Depends(current_org)) -> str:
        actual = _current_role(request)
        if actual is None or ROLE_RANK.get(actual, 0) < needed_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{needed}' required",
            )
        return actual

    return _dep
