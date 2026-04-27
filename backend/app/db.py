from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=20, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    legal_basis: Mapped[str] = mapped_column(Text, default="")
    input_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|running|done|error|cancelled
    synthesis_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthesis_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synthesis_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    collector: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)  # username/email/phone/name/...
    entity_type: Mapped[str] = mapped_column(String(64), index=True)  # Profile/Breach/Company/...
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.7)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)  # for dedup
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectorRun(Base):
    __tablename__ = "collector_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    collector: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/running/ok/error/timeout
    findings_count: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[str] = mapped_column(String(64))
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class AppSetting(Base):
    """Key-value store for user-editable runtime settings (API keys, flags).
    Takes precedence over env vars."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


async def init_db() -> None:
    """Create all tables, required extensions, and apply raw SQL migrations."""
    from pathlib import Path
    import logging
    from sqlalchemy import text

    log = logging.getLogger(__name__)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.is_dir():
        return

    files = sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")
    async with engine.begin() as conn:
        applied = {row[0] for row in (await conn.execute(
            text("SELECT filename FROM schema_migrations")
        )).all()}

    for f in files:
        if f.name in applied:
            continue
        sql = f.read_text(encoding="utf-8")
        try:
            async with engine.begin() as conn:
                # asyncpg rejects multi-statement scripts via prepared protocol,
                # so split on top-level ';' (respecting single quotes, dollar
                # quotes and -- line comments).
                for stmt in _split_sql_statements(sql):
                    if stmt.strip():
                        await conn.exec_driver_sql(stmt)
                await conn.execute(
                    text("INSERT INTO schema_migrations (filename) VALUES (:n)"),
                    {"n": f.name},
                )
            log.info("migration.applied filename=%s", f.name)
        except Exception as e:  # noqa: BLE001
            log.warning("migration.failed filename=%s err=%s", f.name, str(e)[:300])


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.
    Handles ' single-quoted strings, $$ / $tag$ dollar quotes, and -- line comments.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_squote = False
    dollar_tag: str | None = None
    while i < n:
        ch = sql[i]
        # line comment
        if not in_squote and dollar_tag is None and ch == '-' and i + 1 < n and sql[i + 1] == '-':
            j = sql.find('\n', i)
            if j == -1:
                buf.append(sql[i:])
                i = n
            else:
                buf.append(sql[i:j + 1])
                i = j + 1
            continue
        # block comment
        if not in_squote and dollar_tag is None and ch == '/' and i + 1 < n and sql[i + 1] == '*':
            j = sql.find('*/', i + 2)
            if j == -1:
                buf.append(sql[i:])
                i = n
            else:
                buf.append(sql[i:j + 2])
                i = j + 2
            continue
        # dollar quote start/end
        if not in_squote and ch == '$':
            j = sql.find('$', i + 1)
            if j != -1:
                tag = sql[i:j + 1]
                if dollar_tag is None and all(c.isalnum() or c == '_' or c == '$' for c in tag):
                    dollar_tag = tag
                    buf.append(tag)
                    i = j + 1
                    continue
                if dollar_tag is not None and tag == dollar_tag:
                    dollar_tag = None
                    buf.append(tag)
                    i = j + 1
                    continue
        # single quote
        if dollar_tag is None and ch == "'":
            in_squote = not in_squote
            buf.append(ch)
            i += 1
            continue
        # statement terminator
        if not in_squote and dollar_tag is None and ch == ';':
            out.append(''.join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append(''.join(buf))
    return out


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
