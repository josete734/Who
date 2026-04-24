"""Auth is now optional.

Historically required AUTH_TOKEN; user asked for free access. We keep the
dependency callables but they simply no-op so code that declares
`Depends(check_auth)` keeps working.
"""
from __future__ import annotations

from fastapi import Request


def check_auth(request: Request) -> None:
    # Always allow. Kept as a dependency so existing routers don't need to change.
    return None


def check_auth_optional(request: Request) -> bool:
    return True
