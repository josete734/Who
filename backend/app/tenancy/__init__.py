"""Tenancy: orgs, teams, memberships, role-based policy.

Wave 5/E3. Provides multi-org isolation primitives layered atop the
existing api-key auth: an api_key may be bound to an org_id which scopes
all access. Roles: owner > admin > investigator > viewer.
"""
from __future__ import annotations

from .models import (
    Org,
    Team,
    Membership,
    CaseAccess,
    Role,
)
from .policy import can, ROLE_RANK

__all__ = [
    "Org",
    "Team",
    "Membership",
    "CaseAccess",
    "Role",
    "can",
    "ROLE_RANK",
]
