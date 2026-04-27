"""Auth v2: API-key based auth, rate limiting, and admin key management.

Public surface:
    - require_api_key(scopes=[...])  FastAPI dependency
    - admin_router                   APIRouter for /api/admin/keys
    - rate_limit                     sliding-window limiter
"""
from app.security.middleware import require_api_key  # noqa: F401
from app.security.admin_router import router as admin_router  # noqa: F401
from app.security.rate_limit import RateLimiter, rate_limit_dependency  # noqa: F401
