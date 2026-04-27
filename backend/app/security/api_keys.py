"""API key model + CRUD.

Tokens are random 32-byte url-safe strings. We store:
  - sha256(token) as a fast lookup index ("lookup_hash")
  - argon2 hash of the token as the verification secret ("hash")

The plaintext token is shown to the user EXACTLY ONCE on creation.
We never log the full token; only the first 4 chars and a 4-char suffix.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import secrets
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import JSON, DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

logger = logging.getLogger(__name__)
_ph = PasswordHasher()

TOKEN_PREFIX = "osk_"  # "osint key"
TOKEN_BYTES = 32


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128))
    # sha256(token) hex — fast O(1) index lookup, NOT the verification hash
    lookup_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # argon2 hash of token — slow, used to verify
    hash: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _safe_token_repr(token: str) -> str:
    if len(token) < 12:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def lookup_hash_for(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    return _ph.hash(token)


def verify_token(token: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, token)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001
        return False


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def create_api_key(
    session: AsyncSession,
    *,
    name: str,
    scopes: list[str] | None = None,
    rate_limit_per_minute: int = 60,
) -> tuple[ApiKey, str]:
    token = generate_token()
    row = ApiKey(
        name=name,
        lookup_hash=lookup_hash_for(token),
        hash=hash_token(token),
        scopes=scopes or [],
        rate_limit_per_minute=rate_limit_per_minute,
    )
    session.add(row)
    await session.flush()
    logger.info("api_key.created id=%s name=%s token=%s", row.id, name, _safe_token_repr(token))
    return row, token


async def get_by_token(session: AsyncSession, token: str) -> ApiKey | None:
    if not token:
        return None
    lookup = lookup_hash_for(token)
    res = await session.execute(select(ApiKey).where(ApiKey.lookup_hash == lookup))
    row = res.scalar_one_or_none()
    if row is None:
        return None
    if not verify_token(token, row.hash):
        # lookup hash collision (astronomically unlikely) or DB tampering
        return None
    return row


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    res = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return list(res.scalars().all())


async def revoke_api_key(session: AsyncSession, key_id: uuid.UUID) -> bool:
    res = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    row = res.scalar_one_or_none()
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = dt.datetime.now(dt.timezone.utc)
    return True


async def touch_last_used(session: AsyncSession, key_id: uuid.UUID) -> None:
    res = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    row = res.scalar_one_or_none()
    if row is not None:
        row.last_used_at = dt.datetime.now(dt.timezone.utc)
