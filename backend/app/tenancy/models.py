"""Tenancy domain models.

Pydantic-first definitions. The on-disk schema is owned by the SQL
migration `0009_tenancy.sql`; these models are the canonical Python
shape used by the policy engine, the orgs router and tests. We avoid
importing the SQLAlchemy Base here to keep `policy.can` trivially
unit-testable without a database.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    INVESTIGATOR = "investigator"
    VIEWER = "viewer"


RoleLiteral = Literal["owner", "admin", "investigator", "viewer"]


class Org(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    slug: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Team(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    org_id: uuid.UUID
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Membership(BaseModel):
    """A user's role within an org (optionally scoped to a team)."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    org_id: uuid.UUID
    user_id: str  # opaque user identifier (api_key id, email, etc.)
    team_id: Optional[uuid.UUID] = None
    role: Role
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CaseAccess(BaseModel):
    """Per-case ACL override. Falls back to org membership if absent."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    case_id: uuid.UUID
    org_id: uuid.UUID
    user_id: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    role: Role
    created_at: datetime = Field(default_factory=datetime.utcnow)
